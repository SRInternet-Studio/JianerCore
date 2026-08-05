"""Module ownership and Alconna isolation tests for plugin generations."""

import asyncio
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest
from loguru import logger as loguru_logger

from jianer import Client, hyperogger
from jianer.plugins import PluginManager, PluginSetupError
from jianer.plugins.builtin import alconna


def _write(path: Path, body: str) -> None:
    path.write_text(textwrap.dedent(body), encoding="utf-8")


def _directory_plugin(root: Path, value: str, *, broken_setup: bool = False) -> Path:
    plugins = root / "plugins"
    plugin = plugins / "reloadable"
    plugin.mkdir(parents=True)
    _write(plugin / "helper.py", f"VALUE = {value!r}\n")
    failure = "raise RuntimeError('setup failed')" if broken_setup else ""
    _write(
        plugin / "setup.py",
        f"""
        from jianer.plugins import PluginMetadata
        from plugins.reloadable.helper import VALUE

        __plugin_meta__ = PluginMetadata(name="jianerbot-plugin-reloadable")

        def setup(client, manager):
            {failure or "pass"}

        async def on_message(event, actions):
            actions.values.append(VALUE)
            return True
        """,
    )
    return plugins


def _generation_helper(manager: PluginManager):
    matches = [
        module
        for modules in manager._generation_modules.values()
        for name, module in modules.items()
        if name.endswith(".plugins.reloadable.helper")
    ]
    assert len(matches) == 1
    return matches[0]


@pytest.mark.parametrize(
    ("plugin_directory", "plugin_id", "helper_module"),
    [
        ("JianerAI", "jianerbot-plugin-jianer-ai", "observability"),
        ("MaimaiDX", "jianerbot-plugin-maimaidx", "config"),
    ],
)
def test_generation_logs_use_canonical_plugin_module_names(
    tmp_path,
    monkeypatch,
    plugin_directory,
    plugin_id,
    helper_module,
):
    monkeypatch.syspath_prepend(str(tmp_path))
    plugins = tmp_path / "plugins"
    plugin = plugins / plugin_directory
    plugin.mkdir(parents=True)
    _write(
        plugin / f"{helper_module}.py",
        """
        from loguru import logger as direct_loguru_logger

        direct_loguru_logger.info("direct-import-log-name")

        def safe_log_info(logger, message):
            logger.info(message)

        def direct_log_info(message):
            direct_loguru_logger.info(message)
        """,
    )
    _write(
        plugin / "__init__.py",
        f"from .{helper_module} import direct_log_info, safe_log_info\n",
    )
    _write(
        plugin / "setup.py",
        f"""
        from jianer.plugins import PluginMetadata
        from plugins.{plugin_directory} import direct_log_info, safe_log_info

        __plugin_meta__ = PluginMetadata(name={plugin_id!r})

        def log_from_entry(logger, message):
            logger.info(message)
        """,
    )
    records = []
    sink_id = loguru_logger.add(records.append, level="TRACE")
    plugin_logger = hyperogger.Logger("INFO")
    manager = PluginManager(logger=plugin_logger)

    try:
        result = manager.load_plugins(plugins)
        assert result.failed == []
        entry = manager.plugins[plugin_id].module
        helper_runtime_name = entry.safe_log_info.__module__
        assert entry.__name__.startswith(f"jianer_user_plugin_{plugin_directory}_")
        assert helper_runtime_name.startswith("_jianer_plugin_generation_")

        entry.log_from_entry(plugin_logger, "entry-log-name")
        entry.safe_log_info(plugin_logger, "helper-log-name")
        entry.direct_log_info("direct-log-name")

        records_by_message = {
            item.record["message"]: item.record
            for item in records
            if item.record["message"]
            in {
                "entry-log-name",
                "helper-log-name",
                "direct-log-name",
                "direct-import-log-name",
            }
        }
        expected_helper_name = f"{plugin_id}.{helper_module}"
        assert records_by_message["direct-import-log-name"]["name"] == (
            expected_helper_name
        )
        assert records_by_message["entry-log-name"]["name"] == plugin_id
        assert records_by_message["helper-log-name"]["name"] == expected_helper_name
        assert records_by_message["helper-log-name"]["function"] == "safe_log_info"
        assert records_by_message["direct-log-name"]["name"] == expected_helper_name
        assert records_by_message["direct-log-name"]["function"] == "direct_log_info"

        asyncio.run(manager.shutdown())
        records.clear()
        entry.direct_log_info("post-shutdown-log-name")
        post_shutdown = next(
            item.record
            for item in records
            if item.record["message"] == "post-shutdown-log-name"
        )
        assert post_shutdown["name"] == helper_runtime_name
    finally:
        asyncio.run(manager.shutdown())
        loguru_logger.remove(sink_id)


def test_two_alconna_managers_have_isolated_matchers(tmp_path):
    alconna._clear_matchers()
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    _write(
        first / "command.py",
        """
        from jianer.plugins import PluginMetadata
        from jianer.plugins.builtin.alconna import Command
        __plugin_meta__ = PluginMetadata(
            name="jianerbot-plugin-command",
            requires={"jianerbot-plugin-alconna"},
        )

        @Command("ping").handle()
        async def handle(actions):
            actions.values.append("first")
        """,
    )
    _write(
        second / "command.py",
        """
        from jianer.plugins import PluginMetadata
        from jianer.plugins.builtin.alconna import Command
        __plugin_meta__ = PluginMetadata(
            name="jianerbot-plugin-command",
            requires={"jianerbot-plugin-alconna"},
        )

        @Command("ping").handle()
        async def handle(actions):
            actions.values.append("second")
        """,
    )
    first_manager = PluginManager()
    second_manager = PluginManager()
    assert first_manager.load_plugins(first).failed == []
    assert second_manager.load_plugins(second).failed == []
    event = SimpleNamespace(msg_str="ping", group_id=1, user_id=2)

    first_actions = SimpleNamespace(values=[])
    second_actions = SimpleNamespace(values=[])
    assert asyncio.run(first_manager.dispatch(event, first_actions)) is True
    assert asyncio.run(second_manager.dispatch(event, second_actions)) is True
    assert first_actions.values == ["first"]
    assert second_actions.values == ["second"]

    asyncio.run(second_manager.shutdown())
    first_actions.values.clear()
    assert asyncio.run(first_manager.dispatch(event, first_actions)) is True
    assert first_actions.values == ["first"]
    asyncio.run(first_manager.shutdown())


def test_directory_helpers_are_fresh_and_old_shutdown_keeps_new_module(
    tmp_path,
    monkeypatch,
):
    monkeypatch.syspath_prepend(str(tmp_path))
    plugins = _directory_plugin(tmp_path, "old")
    old_manager = PluginManager()
    assert old_manager.load_plugins(plugins).failed == []
    old_helper = _generation_helper(old_manager)

    _write(
        tmp_path / "plugins" / "reloadable" / "helper.py",
        "VALUE = 'new-generation'\n",
    )
    new_manager = PluginManager()
    assert new_manager.load_plugins(plugins).failed == []
    new_helper = _generation_helper(new_manager)
    assert new_helper is not old_helper
    assert "plugins.reloadable.helper" not in sys.modules
    new_entry = new_manager.plugins[
        "jianerbot-plugin-reloadable"
    ].module

    old_actions = SimpleNamespace(values=[])
    new_actions = SimpleNamespace(values=[])
    asyncio.run(old_manager.dispatch(object(), old_actions))
    asyncio.run(new_manager.dispatch(object(), new_actions))
    assert old_actions.values == ["old"]
    assert new_actions.values == ["new-generation"]

    asyncio.run(old_manager.shutdown())
    assert _generation_helper(new_manager) is new_helper
    assert sys.modules[new_entry.__name__] is new_entry
    asyncio.run(new_manager.shutdown())


def test_failed_staged_manager_restores_previous_helper(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(tmp_path))
    plugins = _directory_plugin(tmp_path, "stable")
    client = Client()
    old_manager = PluginManager()
    assert old_manager.load_plugins(plugins).failed == []
    old_manager.setup_client(client)
    client.plugin_manager = old_manager
    stable_helper = _generation_helper(old_manager)

    _write(
        tmp_path / "plugins" / "reloadable" / "helper.py",
        "VALUE = 'broken-generation'\n",
    )
    _write(
        tmp_path / "plugins" / "reloadable" / "setup.py",
        """
        from jianer.plugins import PluginMetadata
        from plugins.reloadable.helper import VALUE
        __plugin_meta__ = PluginMetadata(name="jianerbot-plugin-reloadable")

        def setup(client, manager):
            raise RuntimeError("setup failed")
        """,
    )
    failed_manager = PluginManager()
    assert failed_manager.load_plugins(plugins).failed == []
    with pytest.raises(PluginSetupError):
        failed_manager.setup_client(client, activate=False)
    asyncio.run(failed_manager.shutdown())

    assert _generation_helper(old_manager) is stable_helper
    actions = SimpleNamespace(values=[])
    assert asyncio.run(old_manager.dispatch(object(), actions)) is True
    assert actions.values == ["stable"]
    asyncio.run(client.aclose())


def test_staged_generation_cannot_change_old_lazy_absolute_import(
    tmp_path,
    monkeypatch,
):
    monkeypatch.syspath_prepend(str(tmp_path))
    plugins = tmp_path / "plugins"
    plugin = plugins / "reloadable"
    plugin.mkdir(parents=True)
    _write(plugin / "helper.py", "VALUE = 'old'\n")
    _write(
        plugin / "setup.py",
        """
        from jianer.plugins import PluginMetadata
        __plugin_meta__ = PluginMetadata(name="jianerbot-plugin-reloadable")

        async def on_message(event, actions):
            from plugins.reloadable.helper import VALUE
            actions.values.append(VALUE)
            return True
        """,
    )
    old_manager = PluginManager()
    assert old_manager.load_plugins(plugins).failed == []
    old_manager.activate()

    _write(plugin / "helper.py", "VALUE = 'new'\n")
    new_manager = PluginManager()
    assert new_manager.load_plugins(plugins).failed == []

    old_actions = SimpleNamespace(values=[])
    assert asyncio.run(old_manager.dispatch(object(), old_actions)) is True
    assert old_actions.values == ["old"]
    assert "plugins.reloadable.helper" not in sys.modules

    new_manager.activate()
    new_actions = SimpleNamespace(values=[])
    assert asyncio.run(new_manager.dispatch(object(), new_actions)) is True
    assert new_actions.values == ["new"]
    asyncio.run(old_manager.shutdown())
    asyncio.run(new_manager.shutdown())


def test_generation_router_supports_legacy_dotted_import_as_alias(tmp_path):
    plugins = tmp_path / "plugins"
    plugin = plugins / "AdvancedQuote"
    plugin.mkdir(parents=True)
    _write(plugin / "AdvancedQuote.py", "VALUE = 'legacy-compatible'\n")
    _write(
        plugin / "setup.py",
        """
        import plugins.AdvancedQuote.AdvancedQuote as Quote
        from jianer.plugins import PluginMetadata
        __plugin_meta__ = PluginMetadata(name="jianerbot-plugin-advanced-quote")

        async def on_message(event, actions):
            actions.values.append(Quote.VALUE)
            return True
        """,
    )
    manager = PluginManager()
    assert manager.load_plugins(plugins).failed == []
    manager.activate()
    actions = SimpleNamespace(values=[])
    assert asyncio.run(manager.dispatch(object(), actions)) is True
    assert actions.values == ["legacy-compatible"]
    assert "plugins.AdvancedQuote.AdvancedQuote" not in sys.modules
    asyncio.run(manager.shutdown())


def test_failed_import_removes_only_its_alconna_matchers(tmp_path):
    alconna._clear_matchers()
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    _write(
        plugins / "ghost.py",
        """
        from jianer.plugins import PluginMetadata
        from jianer.plugins.builtin.alconna import Command
        __plugin_meta__ = PluginMetadata(
            name="jianerbot-plugin-ghost",
            requires={"jianerbot-plugin-alconna"},
        )

        @Command("ghost").handle()
        async def ghost(actions):
            actions.values.append("ghost")

        raise RuntimeError("import failed after registration")
        """,
    )
    manager = PluginManager()
    result = manager.load_plugins(plugins)
    assert any("import error" in failure for failure in result.failed)

    actions = SimpleNamespace(values=[])
    event = SimpleNamespace(msg_str="ghost", group_id=1, user_id=2)
    assert asyncio.run(manager.dispatch(event, actions)) is False
    assert actions.values == []
    asyncio.run(manager.shutdown())


def test_shutdown_does_not_remove_shared_dependency_outside_plugin_root(
    tmp_path,
    monkeypatch,
):
    monkeypatch.syspath_prepend(str(tmp_path))
    _write(tmp_path / "shared_dependency.py", "VALUE = 'shared'\n")
    plugins = tmp_path / "plugins"
    plugin = plugins / "consumer"
    plugin.mkdir(parents=True)
    _write(
        plugin / "setup.py",
        """
        import shared_dependency
        from jianer.plugins import PluginMetadata
        __plugin_meta__ = PluginMetadata(name="jianerbot-plugin-consumer")
        """,
    )
    manager = PluginManager()
    assert manager.load_plugins(plugins).failed == []
    shared = sys.modules["shared_dependency"]

    asyncio.run(manager.shutdown())
    assert sys.modules["shared_dependency"] is shared

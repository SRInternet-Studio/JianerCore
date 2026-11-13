"""Tests for JianerCore-owned plugin management commands."""

import asyncio
import textwrap
from pathlib import Path
from types import SimpleNamespace

from jianer import Client, events
from jianer.plugins import PluginManagerState
from jianer.plugins.builtin.alconna import UniMessage as PluginUniMessage
from jianer.unimessage import UniMessage as CoreUniMessage


class FakeActions:
    def __init__(self):
        self.sent = []
        self.calls = []

    async def send(self, message, group_id=None, user_id=None):
        self.sent.append((message, group_id, user_id))
        return SimpleNamespace(data={"message_id": len(self.sent)})


def _write(path: Path, body: str) -> None:
    path.write_text(textwrap.dedent(body), encoding="utf-8")


def _sample_plugin(path: Path, plugin_id: str = "jianerbot-plugin-sample") -> None:
    _write(
        path,
        f"""
        from jianer import events
        from jianer.plugins import PluginMetadata

        __plugin_meta__ = PluginMetadata(name={plugin_id!r})

        def setup(client, manager):
            async def listener_started(event, actions):
                actions.calls.append("listener-start")
            client.subscribe(listener_started, events.HyperListenerStartNotify)

        async def on_message(event, actions):
            actions.calls.append("message")
            return False
        """,
    )


def _event(text: str, user_id: str = "1"):
    return SimpleNamespace(
        msg_str=text,
        group_id="100",
        user_id=user_id,
    )


def test_core_unimessage_is_reexported_by_builtin_alconna():
    assert CoreUniMessage is PluginUniMessage


def test_plugin_view_lists_loaded_and_disabled_plugins(tmp_path):
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    _sample_plugin(plugins / "sample.py")
    _write(
        plugins / "d_off.py",
        """
        from jianer.plugins import PluginMetadata
        __plugin_meta__ = PluginMetadata(name="jianerbot-plugin-off")
        """,
    )
    client = Client()
    assert client.load_plugins(plugins).failed == []

    actions = FakeActions()
    handled = asyncio.run(
        client.dispatch_core_commands(
            _event("~插件视角"),
            actions,
        )
    )

    assert handled is True
    text = actions.sent[0][0][0].text
    assert "已加载插件 (1)" in text
    assert "已禁用插件 (1)" in text
    assert "off.py" in text


def test_core_reload_replays_listener_start(tmp_path):
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    _sample_plugin(plugins / "sample.py")
    client = Client()
    assert client.load_plugins(plugins).failed == []

    actions = FakeActions()
    asyncio.run(
        client.distributor(
            events.HyperListenerStartNotify(0, "start"),
            actions,
        )
    )
    assert actions.calls == ["listener-start"]

    old_manager = client.plugin_manager
    result = asyncio.run(client.reload_plugins(plugins))

    assert result.failed == []
    assert client.plugin_manager is not old_manager
    assert old_manager.state == PluginManagerState.CLOSED
    assert actions.calls == ["listener-start", "listener-start"]
    asyncio.run(client.aclose())


def test_core_toggle_renames_plugin_and_rolls_back_on_reload_failure(tmp_path):
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    _sample_plugin(plugins / "sample.py")
    client = Client()
    assert client.load_plugins(plugins).failed == []
    client.configure_core_commands(is_admin=lambda event: True)
    actions = FakeActions()

    handled = asyncio.run(
        client.dispatch_core_commands(
            _event("~禁用插件 sample"),
            actions,
        )
    )
    assert handled is True
    assert (plugins / "d_sample.py").exists()
    assert "sample.py" in client.disabled_plugins()

    handled = asyncio.run(
        client.dispatch_core_commands(
            _event("~启用插件 sample"),
            actions,
        )
    )
    assert handled is True
    assert (plugins / "sample.py").exists()
    assert "sample.py" not in client.disabled_plugins()
    asyncio.run(client.aclose())


def test_core_commands_require_admin_and_are_group_only(tmp_path):
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    _sample_plugin(plugins / "sample.py")
    client = Client()
    assert client.load_plugins(plugins).failed == []
    client.configure_core_commands(
        is_admin=lambda event: str(event.user_id) == "1",
    )
    actions = FakeActions()

    assert asyncio.run(
        client.dispatch_core_commands(
            SimpleNamespace(msg_str="~插件视角", group_id=None, user_id="1"),
            actions,
        )
    ) is False

    assert asyncio.run(
        client.dispatch_core_commands(
            _event("~重载插件", user_id="2"),
            actions,
        )
    ) is True
    assert "不能这么做" in actions.sent[0][0][0].text

    assert asyncio.run(
        client.dispatch_core_commands(
            _event("~重载插件", user_id="1"),
            actions,
        )
    ) is True
    assert "重载完成" in actions.sent[-1][0][0].text
    asyncio.run(client.aclose())


def test_core_reload_calls_before_reload_hook(tmp_path):
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    _sample_plugin(plugins / "sample.py")
    client = Client()
    assert client.load_plugins(plugins).failed == []
    calls = []
    client.configure_core_commands(
        is_admin=lambda event: True,
        before_reload=lambda: calls.append("before"),
    )

    assert asyncio.run(
        client.dispatch_core_commands(
            _event("~重载插件"),
            FakeActions(),
        )
    ) is True
    assert calls == ["before"]
    asyncio.run(client.aclose())

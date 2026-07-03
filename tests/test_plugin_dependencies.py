"""Tests for dependency-aware plugin loading."""

import textwrap
from pathlib import Path

from jianer.plugins import PluginManager


def _write(path: Path, body: str) -> None:
    path.write_text(textwrap.dedent(body), encoding="utf-8")


def test_dependency_loaded_before_dependent(tmp_path):
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    _write(
        plugins / "main.py",
        """
        from jianer.plugins import PluginMetadata
        __plugin_meta__ = PluginMetadata(name="main", requires={"dep"})
        """,
    )
    _write(
        plugins / "dep.py",
        """
        from jianer.plugins import PluginMetadata
        __plugin_meta__ = PluginMetadata(name="dep")
        """,
    )

    result = PluginManager().load_plugins(plugins)

    assert result.failed == []
    assert result.dependency_order.index("dep") < result.dependency_order.index("main")
    assert set(result.plugin_map) == {"dep", "main"}


def test_missing_dependency_fails_dependent(tmp_path):
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    _write(
        plugins / "main.py",
        """
        from jianer.plugins import PluginMetadata
        __plugin_meta__ = PluginMetadata(name="main", requires={"missing"})
        """,
    )

    result = PluginManager().load_plugins(plugins)

    assert "main" not in result.plugin_map
    assert any("missing dependency: missing" in failure for failure in result.failed)


def test_dependency_import_failure_fails_dependent(tmp_path):
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    _write(
        plugins / "dep.py",
        """
        from jianer.plugins import PluginMetadata
        __plugin_meta__ = PluginMetadata(name="dep")
        raise RuntimeError("boom")
        """,
    )
    _write(
        plugins / "main.py",
        """
        from jianer.plugins import PluginMetadata
        __plugin_meta__ = PluginMetadata(name="main", requires={"dep"})
        """,
    )

    result = PluginManager().load_plugins(plugins)

    assert "dep" not in result.plugin_map
    assert "main" not in result.plugin_map
    assert any("dep (import error: boom)" in failure for failure in result.failed)
    assert any("main (dependency failed to load: dep)" in failure for failure in result.failed)


def test_circular_dependency_fails(tmp_path):
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    _write(
        plugins / "a.py",
        """
        from jianer.plugins import PluginMetadata
        __plugin_meta__ = PluginMetadata(name="a", requires={"b"})
        """,
    )
    _write(
        plugins / "b.py",
        """
        from jianer.plugins import PluginMetadata
        __plugin_meta__ = PluginMetadata(name="b", requires={"a"})
        """,
    )

    result = PluginManager().load_plugins(plugins)

    assert result.plugin_map == {}
    assert any("circular dependency" in failure for failure in result.failed)


def test_duplicate_plugin_id_fails(tmp_path):
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    for name in ("a", "b"):
        _write(
            plugins / f"{name}.py",
            """
            from jianer.plugins import PluginMetadata
            __plugin_meta__ = PluginMetadata(name="same")
            """,
        )

    result = PluginManager().load_plugins(plugins)

    assert result.plugin_map == {}
    assert any("duplicate plugin ID" in failure for failure in result.failed)


def test_legacy_plugin_without_metadata_does_not_enter_new_manager(tmp_path):
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    _write(
        plugins / "legacy.py",
        """
        TRIGGHT_KEYWORD = "hello"
        async def on_message():
            return True
        """,
    )

    result = PluginManager().load_plugins(plugins)

    assert result.plugin_map == {}
    assert result.loaded == []
    assert any("missing PluginMetadata" in warning for warning in result.warnings)

"""Tests for the JianerCore plugin loader."""

import logging
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace

from jianer.plugins import load_plugins


def _make_config(protocol: str = "OneBot"):
    return SimpleNamespace(protocol=protocol)


def _silent_logger():
    logger = logging.getLogger("plugin_loader_test")
    logger.addHandler(logging.NullHandler())
    logger.setLevel(logging.CRITICAL)
    return logger


def _write(path: Path, body: str) -> None:
    path.write_text(textwrap.dedent(body), encoding="utf-8")


def test_loaded_ok_file_plugin(tmp_path):
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    _write(
        plugins_dir / "hello.py",
        """
        TRIGGHT_KEYWORD = "hello"
        HELP_MESSAGE = "hello help"
        async def on_message():
            return True
        """,
    )

    result = load_plugins(_make_config(), _silent_logger(), plugin_folder=plugins_dir)

    assert len(result.plugins) == 1
    assert len(result.loaded) == 1
    assert result.loaded_display == ["hello"]
    assert "hello help" in result.help_text
    assert result.failed == []


def test_disabled_prefix(tmp_path):
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    _write(
        plugins_dir / "d_blocked.py",
        "TRIGGHT_KEYWORD = 'x'\nasync def on_message(): return True\n",
    )

    result = load_plugins(_make_config(), _silent_logger(), plugin_folder=plugins_dir)

    assert result.disabled == ["blocked"]
    assert result.loaded == []


def test_failed_missing_keyword(tmp_path):
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    _write(plugins_dir / "bad.py", "x = 1\n")

    result = load_plugins(_make_config(), _silent_logger(), plugin_folder=plugins_dir)

    assert len(result.failed) == 1
    assert "TRIGGHT_KEYWORD" in result.failed[0]
    assert "on_message" in result.failed[0]
    assert result.loaded == []


def test_failed_wrong_keyword_type(tmp_path):
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    _write(
        plugins_dir / "wrong.py",
        """
        TRIGGHT_KEYWORD = 123
        async def on_message():
            return True
        """,
    )

    result = load_plugins(_make_config(), _silent_logger(), plugin_folder=plugins_dir)

    assert any("TRIGGHT_KEYWORD" in failure for failure in result.failed)


def test_failed_import_error_cleans_module(tmp_path):
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    _write(plugins_dir / "broken.py", "import this_module_does_not_exist_xyz\n")
    before = {name for name in sys.modules if name.startswith("broken_")}

    result = load_plugins(_make_config(), _silent_logger(), plugin_folder=plugins_dir)

    after = {name for name in sys.modules if name.startswith("broken_")}
    assert len(result.failed) == 1
    assert result.loaded == []
    assert after == before


def test_folder_plugin_with_setup(tmp_path):
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    sub = plugins_dir / "myplug"
    sub.mkdir()
    _write(
        sub / "setup.py",
        """
        TRIGGHT_KEYWORD = "cmd"
        async def on_message():
            return True
        """,
    )

    result = load_plugins(_make_config(), _silent_logger(), plugin_folder=plugins_dir)

    assert "myplug" in result.loaded_display
    assert result.disabled == []


def test_folder_plugin_missing_setup(tmp_path):
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    (plugins_dir / "noentry").mkdir()

    result = load_plugins(_make_config(), _silent_logger(), plugin_folder=plugins_dir)

    assert any("setup.py" in failure for failure in result.failed)


def test_feishu_incompatible_filtered(tmp_path):
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    _write(
        plugins_dir / "CheckAccount.py",
        "TRIGGHT_KEYWORD = 'x'\nasync def on_message(): return True\n",
    )

    result = load_plugins(_make_config(protocol="Feishu"), _silent_logger(), plugin_folder=plugins_dir)

    assert "CheckAccount" in result.disabled
    assert result.loaded == []


def test_pycache_skipped(tmp_path):
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    (plugins_dir / "__pycache__").mkdir()

    result = load_plugins(_make_config(), _silent_logger(), plugin_folder=plugins_dir)

    assert result.loaded == []
    assert result.disabled == []
    assert result.failed == []


def test_pyw_extension_handled(tmp_path):
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    _write(
        plugins_dir / "winonly.pyw",
        "TRIGGHT_KEYWORD = 'x'\nasync def on_message(): return True\n",
    )

    result = load_plugins(_make_config(), _silent_logger(), plugin_folder=plugins_dir)

    assert result.loaded_display == ["winonly"]


def test_help_text_format(tmp_path):
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    _write(
        plugins_dir / "p1.py",
        """
        TRIGGHT_KEYWORD = "a"
        HELP_MESSAGE = "line1\\nline2"
        async def on_message():
            return True
        """,
    )

    result = load_plugins(_make_config(), _silent_logger(), plugin_folder=plugins_dir)

    assert "\n       line1" in result.help_text
    assert "\n       line2" in result.help_text


def test_create_missing_false_reports_missing_without_creating(tmp_path):
    plugins_dir = tmp_path / "missing_plugins"

    result = load_plugins(plugin_folder=plugins_dir, create_missing=False)

    assert not plugins_dir.exists()
    assert len(result.failed) == 1
    assert str(plugins_dir) in result.failed[0]


def test_custom_plugin_folder_is_used(tmp_path):
    custom_dir = tmp_path / "custom_plugins"
    default_dir = tmp_path / "plugins"
    custom_dir.mkdir()
    default_dir.mkdir()
    _write(
        custom_dir / "custom.py",
        "TRIGGHT_KEYWORD = 'x'\nasync def on_message(): return True\n",
    )
    _write(
        default_dir / "default.py",
        "TRIGGHT_KEYWORD = 'x'\nasync def on_message(): return True\n",
    )

    result = load_plugins(plugin_folder=custom_dir)

    assert result.loaded_display == ["custom"]

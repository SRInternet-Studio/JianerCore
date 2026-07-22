"""End-to-end tests for the bundled JianerCore plugin-creation skill."""

import subprocess
import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = REPOSITORY_ROOT / "skills" / "create-jianercore-plugin"
SCAFFOLD_SCRIPT = SKILL_DIR / "scripts" / "scaffold_plugin.py"
VALIDATE_SCRIPT = SKILL_DIR / "scripts" / "validate_plugin.py"


def _run(*args: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-X", "utf8", "-B", *(str(arg) for arg in args)],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


def _assert_ok(result: subprocess.CompletedProcess[str]) -> None:
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"


def test_alconna_scaffold_loads_and_generated_test_dispatches(tmp_path):
    plugins = tmp_path / "plugins"
    tests = tmp_path / "tests"

    scaffold = _run(
        SCAFFOLD_SCRIPT,
        "ping",
        "--plugins-dir",
        plugins,
        "--tests-dir",
        tests,
        "--command",
        "ping",
        "--reply",
        "pong",
        "--description",
        "收到 ping 时回复 pong",
        "--usage",
        "发送 ping",
    )
    _assert_ok(scaffold)

    plugin = plugins / "ping.py"
    generated_test = tests / "test_ping_plugin.py"
    assert plugin.is_file()
    assert generated_test.is_file()
    assert "name='jianerbot-plugin-ping'" in plugin.read_text(encoding="utf-8")

    static = _run(VALIDATE_SCRIPT, plugin)
    _assert_ok(static)
    assert "passed static validation" in static.stdout

    dynamic = _run(VALIDATE_SCRIPT, plugin, "--load")
    _assert_ok(dynamic)
    assert "loaded through PluginManager" in dynamic.stdout

    dispatch = _run(
        "-m",
        "pytest",
        generated_test,
        "-q",
        "--rootdir",
        tests,
        "-p",
        "no:cacheprovider",
    )
    _assert_ok(dispatch)
    assert "1 passed" in dispatch.stdout


def test_raw_dispatch_scaffold_covers_group_private_and_non_match(tmp_path):
    plugins = tmp_path / "plugins"
    tests = tmp_path / "tests"

    scaffold = _run(
        SCAFFOLD_SCRIPT,
        "raw-hello",
        "--plugins-dir",
        plugins,
        "--tests-dir",
        tests,
        "--mode",
        "dispatch",
        "--command",
        "hello",
        "--reply",
        "world",
    )
    _assert_ok(scaffold)

    plugin = plugins / "raw_hello.py"
    generated_test = tests / "test_raw_hello_plugin.py"
    dynamic = _run(VALIDATE_SCRIPT, plugin, "--load")
    _assert_ok(dynamic)

    dispatch = _run(
        "-m",
        "pytest",
        generated_test,
        "-q",
        "--rootdir",
        tests,
        "-p",
        "no:cacheprovider",
    )
    _assert_ok(dispatch)
    assert "1 passed" in dispatch.stdout


def test_scaffolder_rejects_invalid_names_commands_and_overwrites(tmp_path):
    plugins = tmp_path / "plugins"

    invalid_name = _run(SCAFFOLD_SCRIPT, "Bad_Name", "--plugins-dir", plugins)
    assert invalid_name.returncode == 2
    assert "plugin name must use lowercase" in invalid_name.stderr

    invalid_command = _run(
        SCAFFOLD_SCRIPT,
        "echo",
        "--plugins-dir",
        plugins,
        "--command",
        "echo <text>",
    )
    assert invalid_command.returncode == 2
    assert "baseline command must be one non-whitespace token" in invalid_command.stderr

    first = _run(SCAFFOLD_SCRIPT, "ping", "--plugins-dir", plugins)
    _assert_ok(first)
    second = _run(SCAFFOLD_SCRIPT, "ping", "--plugins-dir", plugins)
    assert second.returncode == 2
    assert "refusing to overwrite" in second.stderr

    occupied_directory = plugins / "tools"
    occupied_directory.mkdir()
    directory_result = _run(
        SCAFFOLD_SCRIPT,
        "tools",
        "--plugins-dir",
        plugins,
        "--layout",
        "directory",
    )
    assert directory_result.returncode == 2
    assert "inside existing path" in directory_result.stderr


def test_validator_rejects_metadata_and_command_shortcut_traps(tmp_path):
    annotated = tmp_path / "annotated.py"
    annotated.write_text(
        "from jianer.plugins import PluginMetadata\n"
        "__plugin_meta__: PluginMetadata = PluginMetadata(name='jianerbot-plugin-bad')\n",
        encoding="utf-8",
    )
    annotated_result = _run(VALIDATE_SCRIPT, annotated)
    assert annotated_result.returncode == 1
    assert "plain top-level assignment" in annotated_result.stderr

    multi = tmp_path / "multi.py"
    multi.write_text(
        "from jianer.plugins import PluginMetadata\n"
        "from jianer.plugins.builtin.alconna import Command\n"
        "__plugin_meta__ = PluginMetadata(\n"
        "    name='jianerbot-plugin-multi',\n"
        "    requires={'jianerbot-plugin-alconna'},\n"
        ")\n"
        "@Command('pair <a> <b>').handle()\n"
        "async def handle(a: str, b: str):\n"
        "    return True\n",
        encoding="utf-8",
    )
    multi_result = _run(VALIDATE_SCRIPT, multi)
    assert multi_result.returncode == 1
    assert "use an explicit Alconna object" in multi_result.stderr


def test_skill_metadata_and_resources_are_complete():
    skill_text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    assert "TODO" not in skill_text
    assert len(skill_text.splitlines()) < 200
    assert "name: create-jianercore-plugin" in skill_text
    assert "JianerCore" in skill_text
    assert "$create-jianercore-plugin" in (SKILL_DIR / "agents" / "openai.yaml").read_text(
        encoding="utf-8"
    )
    for relative_path in (
        "references/plugin-contract.md",
        "assets/alconna_plugin.py.tmpl",
        "assets/dispatch_plugin.py.tmpl",
        "scripts/scaffold_plugin.py",
        "scripts/validate_plugin.py",
    ):
        assert (SKILL_DIR / relative_path).is_file()

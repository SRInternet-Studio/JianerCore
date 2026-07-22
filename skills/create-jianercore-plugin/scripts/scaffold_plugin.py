"""Create a conservative JianerCore plugin baseline from bundled templates."""

from __future__ import annotations

import argparse
import keyword
import os
import re
import sys
from pathlib import Path
from string import Template


PLUGIN_PREFIX = "jianerbot-plugin-"
PLUGIN_PATTERN = re.compile(r"^jianerbot-plugin-[a-z0-9]+(?:-[a-z0-9]+)*$")
ASSET_DIR = Path(__file__).resolve().parent.parent / "assets"


class ScaffoldError(ValueError):
    pass


def normalize_plugin_name(value: str) -> tuple[str, str, str]:
    candidate = value.strip()
    plugin_id = candidate if candidate.startswith(PLUGIN_PREFIX) else f"{PLUGIN_PREFIX}{candidate}"
    if not PLUGIN_PATTERN.fullmatch(plugin_id):
        raise ScaffoldError(
            "plugin name must use lowercase letters, digits, and single hyphens "
            f"and resolve to {PLUGIN_PREFIX}{{name}}"
        )
    slug = plugin_id[len(PLUGIN_PREFIX):]
    module_name = slug.replace("-", "_")
    if not module_name.isidentifier() or keyword.iskeyword(module_name):
        module_name = f"plugin_{module_name}"
    return plugin_id, slug, module_name


def validate_command(value: str) -> str:
    command = value.strip()
    if not command or any(char.isspace() for char in command) or "<" in command or ">" in command:
        raise ScaffoldError(
            "the baseline command must be one non-whitespace token without placeholders; "
            "scaffold first, then use explicit JianerCore command patterns for arguments"
        )
    return command


def render_asset(name: str, values: dict[str, str]) -> str:
    path = ASSET_DIR / name
    try:
        source = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ScaffoldError(f"cannot read bundled template {path}: {exc}") from exc
    return Template(source).substitute(values)


def plugin_path_expression(load_target: Path, tests_dir: Path) -> str:
    try:
        relative = os.path.relpath(load_target.resolve(), tests_dir.resolve())
    except ValueError:
        return f"Path({str(load_target.resolve())!r})"
    return f"(Path(__file__).resolve().parent / {relative!r}).resolve()"


def build_files(args: argparse.Namespace) -> dict[Path, str]:
    plugin_id, slug, module_name = normalize_plugin_name(args.name)
    command = validate_command(args.command or slug)
    description = args.description or f"JianerCore plugin {plugin_id}"
    usage = args.usage or f"Send {command}"
    reply = args.reply or f"{slug} is ready"

    plugins_dir = args.plugins_dir.resolve()
    if args.layout == "single":
        entry_target = plugins_dir / f"{module_name}.py"
        load_target = entry_target
    else:
        load_target = plugins_dir / module_name
        if load_target.exists():
            raise ScaffoldError(f"refusing to add a directory plugin inside existing path: {load_target}")
        entry_target = load_target / "setup.py"

    common_values = {
        "plugin_id": plugin_id,
        "plugin_id_literal": repr(plugin_id),
        "description_literal": repr(description),
        "usage_literal": repr(usage),
        "command_literal": repr(command),
        "reply_literal": repr(reply),
        "handler_name": f"handle_{module_name}",
    }
    plugin_template = "alconna_plugin.py.tmpl" if args.mode == "alconna" else "dispatch_plugin.py.tmpl"
    files = {entry_target: render_asset(plugin_template, common_values)}

    if args.tests_dir is not None:
        tests_dir = args.tests_dir.resolve()
        test_values = dict(common_values)
        test_values.update(
            {
                "plugin_path_expression": plugin_path_expression(load_target, tests_dir),
                "test_name": f"{module_name}_plugin_dispatch",
            }
        )
        test_template = (
            "test_alconna_plugin.py.tmpl" if args.mode == "alconna" else "test_dispatch_plugin.py.tmpl"
        )
        test_target = tests_dir / f"test_{module_name}_plugin.py"
        files[test_target] = render_asset(test_template, test_values)
    return files


def write_files(files: dict[Path, str]) -> None:
    conflicts = [path for path in files if path.exists()]
    if conflicts:
        joined = ", ".join(str(path) for path in conflicts)
        raise ScaffoldError(f"refusing to overwrite existing path(s): {joined}")
    for path, content in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        print(f"CREATED {path}")


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("name", help="short plugin name or canonical jianerbot-plugin-* ID")
    parser.add_argument("--plugins-dir", type=Path, default=Path("plugins"))
    parser.add_argument("--tests-dir", type=Path)
    parser.add_argument("--mode", choices=("alconna", "dispatch"), default="alconna")
    parser.add_argument("--layout", choices=("single", "directory"), default="single")
    parser.add_argument("--command", help="one-token baseline command; defaults to the plugin slug")
    parser.add_argument("--reply", help="static reply text")
    parser.add_argument("--description", help="PluginMetadata description")
    parser.add_argument("--usage", help="PluginMetadata usage")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = create_parser()
    args = parser.parse_args(argv)
    try:
        write_files(build_files(args))
    except ScaffoldError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

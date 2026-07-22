"""Validate JianerCore plugin metadata and optionally load it with PluginManager."""

from __future__ import annotations

import argparse
import ast
import keyword
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


PLUGIN_PATTERN = re.compile(r"^jianerbot-plugin-[a-z0-9]+(?:-[a-z0-9]+)*$")
ALCONNA_PLUGIN_ID = "jianerbot-plugin-alconna"


@dataclass
class Report:
    entry: Path
    plugin_id: str | None = None
    requires: set[str] = field(default_factory=set)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def resolve_entry(target: Path) -> Path:
    if target.is_dir():
        return target / "setup.py"
    return target


def callable_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def literal_metadata(call: ast.Call, report: Report) -> dict[str, Any] | None:
    if callable_name(call.func) != "PluginMetadata":
        report.errors.append("__plugin_meta__ must directly call PluginMetadata(...)")
        return None
    if len(call.args) > 3:
        report.errors.append("PluginMetadata accepts at most three positional literals; use requires=...")
        return None

    values: dict[str, Any] = {}
    positional = ("name", "description", "usage")
    for index, argument in enumerate(call.args):
        try:
            values[positional[index]] = ast.literal_eval(argument)
        except (TypeError, ValueError):
            report.errors.append(f"PluginMetadata {positional[index]} must be an AST literal")
            return None
    allowed = {"name", "description", "usage", "requires"}
    for keyword in call.keywords:
        if keyword.arg is None or keyword.arg not in allowed:
            report.errors.append(f"unsupported PluginMetadata keyword: {keyword.arg}")
            return None
        try:
            values[keyword.arg] = ast.literal_eval(keyword.value)
        except (TypeError, ValueError):
            report.errors.append(f"PluginMetadata {keyword.arg} must be an AST literal")
            return None
    return values


def find_metadata(tree: ast.Module, report: Report) -> dict[str, Any] | None:
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id == "__plugin_meta__":
                report.errors.append("__plugin_meta__ must use a plain top-level assignment, not an annotation")
                return None
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == "__plugin_meta__" for target in node.targets):
            continue
        if not isinstance(node.value, ast.Call):
            report.errors.append("__plugin_meta__ must directly call PluginMetadata(...)")
            return None
        return literal_metadata(node.value, report)
    report.errors.append("missing top-level __plugin_meta__ = PluginMetadata(...) assignment")
    return None


def command_patterns(tree: ast.Module) -> list[str]:
    patterns: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or callable_name(node.func) != "Command" or not node.args:
            continue
        try:
            value = ast.literal_eval(node.args[0])
        except (TypeError, ValueError):
            continue
        if isinstance(value, str):
            patterns.append(value)
    return patterns


def validate_command_patterns(patterns: list[str], report: Report) -> None:
    for pattern in patterns:
        parts = pattern.split()
        placeholders = [part for part in parts[1:] if part.startswith("<") and part.endswith(">")]
        reliable = len(parts) == 1 or (len(parts) == 2 and len(placeholders) == 1)
        if not reliable:
            report.errors.append(
                f"string Command pattern {pattern!r} exceeds the reliable shortcut; use an explicit Alconna object"
            )
            continue
        if placeholders:
            argument_name = placeholders[0][1:-1]
            if not argument_name.isidentifier() or keyword.iskeyword(argument_name):
                report.errors.append(f"Command placeholder must be a Python-safe injected name: {placeholders[0]!r}")


def validate_static(target: Path) -> Report:
    entry = resolve_entry(target)
    report = Report(entry=entry)
    if not entry.is_file():
        report.errors.append(f"plugin entry does not exist: {entry}")
        return report
    if entry.suffix not in {".py", ".pyw"}:
        report.errors.append(f"plugin entry must be .py or .pyw: {entry}")
        return report

    try:
        source = entry.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(entry))
    except (OSError, UnicodeError, SyntaxError) as exc:
        report.errors.append(f"cannot parse plugin entry: {exc}")
        return report

    values = find_metadata(tree, report)
    if values is None:
        return report
    plugin_id = values.get("name")
    if not isinstance(plugin_id, str) or not PLUGIN_PATTERN.fullmatch(plugin_id):
        report.errors.append("metadata name must match jianerbot-plugin-{lowercase-name}")
    else:
        report.plugin_id = plugin_id

    for field_name in ("description", "usage"):
        value = values.get(field_name, "")
        if not isinstance(value, str):
            report.errors.append(f"metadata {field_name} must be a string literal")

    requires = values.get("requires", set())
    if isinstance(requires, dict) and not requires:
        requires = set()
    if not isinstance(requires, (set, list, tuple)) or not all(isinstance(item, str) for item in requires):
        report.errors.append("metadata requires must be a literal set/list/tuple of plugin ID strings")
    else:
        report.requires = set(requires)
        invalid_dependencies = sorted(item for item in report.requires if not PLUGIN_PATTERN.fullmatch(item))
        if invalid_dependencies:
            report.errors.append(f"requires contains non-canonical plugin ID(s): {invalid_dependencies}")

    patterns = command_patterns(tree)
    validate_command_patterns(patterns, report)
    uses_command = any(callable_name(node.func) == "Command" for node in ast.walk(tree) if isinstance(node, ast.Call))
    if uses_command and ALCONNA_PLUGIN_ID not in report.requires:
        report.errors.append(f"Command usage requires {ALCONNA_PLUGIN_ID!r} in PluginMetadata.requires")

    top_level_functions = {
        node.name: node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    on_message = top_level_functions.get("on_message")
    if on_message is not None and not isinstance(on_message, ast.AsyncFunctionDef):
        report.errors.append("on_message must be declared with async def")
    setup = top_level_functions.get("setup")
    if isinstance(setup, ast.AsyncFunctionDef):
        report.errors.append("setup must be synchronous because PluginManager does not await it")
    if not uses_command and on_message is None and setup is None:
        report.warnings.append("plugin exposes no Command, on_message, or setup hook")

    has_future_annotations = any(
        isinstance(node, ast.ImportFrom)
        and node.module == "__future__"
        and any(alias.name == "annotations" for alias in node.names)
        for node in tree.body
    )
    if has_future_annotations and patterns:
        report.warnings.append(
            "future annotations can break MultiVar tuple-to-str normalization in current JianerCore command handlers"
        )
    return report


def dynamic_load(target: Path, report: Report, plugins_dir: Path | None) -> None:
    if report.plugin_id is None or report.errors:
        return
    try:
        from jianer.plugins import PluginManager
    except Exception as exc:
        report.errors.append(f"cannot import jianer.plugins for load check: {exc}")
        return

    manager = PluginManager()
    if plugins_dir is None:
        plugin = manager.load_plugin(target)
    else:
        result = manager.load_plugins(plugins_dir, create_missing=False)
        plugin = result.plugin_map.get(report.plugin_id)
    for warning in manager.warnings:
        report.warnings.append(f"PluginManager warning: {warning}")
    if plugin is None:
        details = "; ".join(manager.failed + manager.warnings) or "no manager diagnostic"
        report.errors.append(f"PluginManager did not load {report.plugin_id}: {details}")
    else:
        for failure in manager.failed:
            report.warnings.append(f"PluginManager sibling failure: {failure}")


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plugin_path", type=Path, help="plugin .py/.pyw file or directory containing setup.py")
    parser.add_argument("--load", action="store_true", help="also import through PluginManager")
    parser.add_argument("--plugins-dir", type=Path, help="discover this folder before checking the target ID")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = create_parser().parse_args(argv)
    target = args.plugin_path.resolve()
    report = validate_static(target)
    if args.load:
        dynamic_load(target, report, args.plugins_dir.resolve() if args.plugins_dir else None)
    for warning in report.warnings:
        print(f"WARNING: {warning}")
    for error in report.errors:
        print(f"ERROR: {error}", file=sys.stderr)
    if report.errors:
        return 1
    print(f"OK: {report.plugin_id} passed static validation at {report.entry}")
    if args.load:
        print(f"OK: {report.plugin_id} loaded through PluginManager")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Dependency-aware plugin manager."""

from __future__ import annotations

import ast
import importlib
import importlib.util
import sys
import traceback
import uuid
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

from .loader import DISABLED_PREFIX, PLUGIN_EXTENSIONS, PLUGIN_FOLDER, LoadResult
from .metadata import PLUGIN_NAME_PREFIX, Plugin, PluginMetadata, is_valid_plugin_name

BUILTIN_PLUGINS = {
    "jianerbot-plugin-alconna": "jianer.plugins.builtin.alconna",
    # Compatibility alias for plugins written before the canonical id was renamed.
    "jianer-alconna": "jianer.plugins.builtin.alconna",
}


@dataclass
class _PluginCandidate:
    key: str
    source: str | Path
    metadata: PluginMetadata | None
    display_name: str
    module_name: str | None = None

    @property
    def plugin_id(self) -> str | None:
        return self.metadata.name if self.metadata is not None else None


class PluginManager:
    """Load and dispatch dependency-aware Jianer plugins."""

    def __init__(
        self,
        logger: Any | None = None,
        *,
        builtin_plugins: dict[str, str] | None = None,
    ) -> None:
        self.logger = logger or _create_default_logger()
        self.builtin_plugins = dict(BUILTIN_PLUGINS)
        if builtin_plugins:
            self.builtin_plugins.update(builtin_plugins)
        self.plugins: dict[str, Plugin] = {}
        self.dependency_order: list[str] = []
        self.warnings: list[str] = []
        self.failed: list[str] = []
        self._candidates: dict[str, _PluginCandidate] = {}
        self._invalid_plugin_reasons: dict[str, str] = {}
        self._loading: set[str] = set()

    def load_plugins(
        self,
        *plugin_folders: str | Path,
        create_missing: bool = True,
    ) -> LoadResult:
        """Discover and load all dependency-aware plugins in plugin folders."""

        if not plugin_folders:
            plugin_folders = (PLUGIN_FOLDER,)

        result = LoadResult()
        for plugin_folder in plugin_folders:
            folder = Path(plugin_folder)
            if not folder.exists():
                if create_missing:
                    folder.mkdir(parents=True, exist_ok=True)
                else:
                    self._fail(f"{folder} (plugin directory does not exist)")
                    continue
            if not folder.is_dir():
                self._fail(f"{folder} (plugin path is not a directory)")
                continue
            self._discover_folder(folder)

        for plugin_id in list(self._candidates):
            self.load_plugin(plugin_id)

        self._fill_result(result)
        self._log_load_result(result)
        return result

    def load_plugin(self, plugin: str | Path) -> Plugin | None:
        """Load one plugin by built-in id, discovered id, module path, or file path."""

        candidate = self._resolve_candidate(plugin)
        if candidate is None:
            key = str(plugin)
            self._fail(f"{key} (plugin does not exist or is missing PluginMetadata)")
            return None
        plugin_id = candidate.plugin_id
        if plugin_id is None:
            self._warn(f"{candidate.display_name} (missing PluginMetadata; skipped)")
            return None
        return self._load_candidate(plugin_id, candidate, [])

    async def dispatch(self, event: Any, actions: Any) -> bool:
        """Dispatch an event to loaded plugins that expose a dispatch coroutine."""

        handled = False
        for plugin_id in list(self.dependency_order):
            plugin = self.plugins.get(plugin_id)
            if plugin is None:
                continue
            dispatcher = getattr(plugin.module, "dispatch", None)
            if dispatcher is None:
                continue
            try:
                response = dispatcher(event, actions)
                if hasattr(response, "__await__"):
                    response = await response
                if response is True:
                    handled = True
                    break
            except Exception:
                self._log("error", f"插件派发失败：{plugin_id}\n{traceback.format_exc()}")
        return handled

    def setup_client(self, client: Any) -> None:
        """Run optional plugin setup hooks against a Jianer Client."""

        for plugin_id in list(self.dependency_order):
            plugin = self.plugins.get(plugin_id)
            if plugin is None:
                continue
            setup = getattr(plugin.module, "setup", None)
            if callable(setup):
                setup(client, self)

    def _discover_folder(self, folder: Path) -> None:
        for entry in folder.iterdir():
            filename = entry.name
            if filename == "__pycache__" or filename.startswith(DISABLED_PREFIX):
                continue
            candidate = self._candidate_from_path(entry)
            if candidate is None:
                continue
            if candidate.plugin_id is None:
                self._warn(f"{candidate.display_name} (missing PluginMetadata; skipped)")
                continue
            self._add_candidate(candidate)

    def _candidate_from_path(self, entry: Path) -> _PluginCandidate | None:
        if entry.is_dir():
            entry_file = entry / "setup.py"
            if not entry_file.exists():
                self._fail(f"{entry.name} (entry error: missing setup.py)")
                return None
            metadata = _read_plugin_metadata(entry_file)
            return _PluginCandidate(str(entry.resolve()), entry_file, metadata, entry.name)
        if entry.suffix in PLUGIN_EXTENSIONS:
            metadata = _read_plugin_metadata(entry)
            return _PluginCandidate(str(entry.resolve()), entry, metadata, entry.stem)
        return None

    def _resolve_candidate(self, plugin: str | Path) -> _PluginCandidate | None:
        if isinstance(plugin, Path) or Path(str(plugin)).exists():
            path = Path(plugin)
            if path.is_dir() or path.suffix in PLUGIN_EXTENSIONS:
                candidate = self._candidate_from_path(path)
                if candidate and candidate.plugin_id:
                    self._add_candidate(candidate)
                return candidate
            return None

        plugin_key = str(plugin)
        if plugin_key in self.plugins:
            loaded = self.plugins[plugin_key]
            return _PluginCandidate(plugin_key, loaded.source, loaded.metadata, plugin_key)
        if plugin_key in self._candidates:
            return self._candidates[plugin_key]
        if plugin_key in self.builtin_plugins:
            module_name = self.builtin_plugins[plugin_key]
            metadata = _read_module_metadata(module_name)
            if metadata is None:
                metadata = PluginMetadata(name=plugin_key)
            candidate = _PluginCandidate(plugin_key, module_name, metadata, plugin_key, module_name)
            self._add_candidate(candidate)
            return candidate

        metadata = _read_module_metadata(plugin_key)
        if metadata is None:
            return None
        candidate = _PluginCandidate(plugin_key, plugin_key, metadata, metadata.name, plugin_key)
        self._add_candidate(candidate)
        return candidate

    def _add_candidate(self, candidate: _PluginCandidate) -> None:
        plugin_id = candidate.plugin_id
        if plugin_id is None:
            return
        if not is_valid_plugin_name(plugin_id):
            reason = f"{plugin_id} (invalid plugin ID: use {PLUGIN_NAME_PREFIX}{{name}})"
            self._fail(reason)
            self._invalid_plugin_reasons[plugin_id] = reason
            return
        existing = self._candidates.get(plugin_id)
        if existing and str(existing.source) != str(candidate.source):
            self._fail(f"{plugin_id} (duplicate plugin ID: {existing.source} and {candidate.source})")
            self._invalid_plugin_reasons[plugin_id] = f"{plugin_id} (duplicate plugin ID)"
            return
        self._candidates[plugin_id] = candidate

    def _load_candidate(
        self,
        plugin_id: str,
        candidate: _PluginCandidate,
        stack: list[str],
    ) -> Plugin | None:
        invalid_reason = self._invalid_plugin_reasons.get(plugin_id)
        if invalid_reason is not None:
            self._fail(invalid_reason)
            return None
        if plugin_id in self.plugins:
            return self.plugins[plugin_id]
        if plugin_id in self._loading:
            cycle = " -> ".join([*stack, plugin_id])
            self._fail(f"{plugin_id} (circular dependency: {cycle})")
            return None

        metadata = candidate.metadata
        if metadata is None:
            self._warn(f"{candidate.display_name} (missing PluginMetadata; skipped)")
            return None

        self._loading.add(plugin_id)
        for dependency in metadata.requires:
            dep_candidate = self._resolve_candidate(dependency)
            if dep_candidate is None or dep_candidate.plugin_id is None:
                self._fail(f"{plugin_id} (missing dependency: {dependency})")
                self._loading.discard(plugin_id)
                return None
            if self._load_candidate(dep_candidate.plugin_id, dep_candidate, [*stack, plugin_id]) is None:
                self._fail(f"{plugin_id} (dependency failed to load: {dependency})")
                self._loading.discard(plugin_id)
                return None

        module = self._import_candidate(candidate)
        self._loading.discard(plugin_id)
        if module is None:
            return None

        runtime_metadata = getattr(module, "__plugin_meta__", metadata)
        if not isinstance(runtime_metadata, PluginMetadata):
            self._fail(f"{plugin_id} (__plugin_meta__ must be PluginMetadata)")
            return None
        if not is_valid_plugin_name(runtime_metadata.name):
            self._fail(f"{runtime_metadata.name} (invalid plugin ID: use {PLUGIN_NAME_PREFIX}{{name}})")
            return None
        if runtime_metadata.name != plugin_id:
            self._fail(f"{plugin_id} (plugin ID mismatch: {runtime_metadata.name})")
            return None
        if plugin_id in self.plugins:
            self._fail(f"{plugin_id} (duplicate plugin ID)")
            return None

        loaded = Plugin(
            name=plugin_id,
            module=module,
            metadata=runtime_metadata,
            source=str(candidate.source),
            dependencies=runtime_metadata.requires,
        )
        self.plugins[plugin_id] = loaded
        self.dependency_order.append(plugin_id)
        self._log("info", f"已加载插件：{plugin_id}")
        return loaded

    def _import_candidate(self, candidate: _PluginCandidate) -> ModuleType | None:
        try:
            if candidate.module_name is not None:
                return importlib.import_module(candidate.module_name)
            path = Path(candidate.source)
            unique_module_name = f"jianer_user_plugin_{candidate.display_name}_{uuid.uuid4().hex}"
            spec = importlib.util.spec_from_file_location(unique_module_name, path)
            if spec is None or spec.loader is None:
                self._fail(f"{candidate.display_name} (entry error: unable to create import spec)")
                return None
            module = importlib.util.module_from_spec(spec)
            sys.modules[unique_module_name] = module
            spec.loader.exec_module(module)
            return module
        except Exception as exc:
            self._fail(f"{candidate.display_name} (import error: {exc})")
            if "unique_module_name" in locals():
                sys.modules.pop(unique_module_name, None)
            self._log("error", f"插件导入失败：{candidate.display_name}\n{traceback.format_exc()}")
            return None

    def _fill_result(self, result: LoadResult) -> None:
        result.plugins.extend(plugin.module for plugin in self.plugins.values())
        result.loaded.extend(self.dependency_order)
        result.loaded_display.extend(self.dependency_order)
        result.failed.extend(self.failed)
        result.plugin_map.update(self.plugins)
        result.dependency_order.extend(self.dependency_order)
        result.warnings.extend(self.warnings)

    def _fail(self, message: str) -> None:
        if message not in self.failed:
            self.failed.append(message)

    def _warn(self, message: str) -> None:
        if message not in self.warnings:
            self.warnings.append(message)

    def _log(self, level: str, message: str) -> None:
        logger_method = getattr(self.logger, level, None)
        if callable(logger_method):
            logger_method(message)

    def _log_load_result(self, result: LoadResult) -> None:
        self._log(
            "info",
            f"插件加载完成：{len(result.loaded)} 成功，{len(result.failed)} 失败，{len(result.warnings)} 警告",
        )
        if not result.loaded:
            self._log("info", "未加载任何新式插件")
        for warning in result.warnings:
            self._log("warning", f"插件加载警告：{warning}")
        for failure in result.failed:
            self._log("error", f"插件加载失败：{failure}")


def _create_default_logger() -> Any:
    from .. import hyperogger

    return hyperogger.Logger()


def _read_module_metadata(module_name: str) -> PluginMetadata | None:
    try:
        spec = importlib.util.find_spec(module_name)
    except (ImportError, ValueError):
        return None
    if spec is None or spec.origin is None or spec.origin in {"built-in", "namespace"}:
        return None
    return _read_plugin_metadata(Path(spec.origin))


def _read_plugin_metadata(path: Path) -> PluginMetadata | None:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return None
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == "__plugin_meta__" for target in node.targets):
            continue
        return _metadata_from_node(node.value)
    return None


def _metadata_from_node(node: ast.AST) -> PluginMetadata | None:
    if not isinstance(node, ast.Call):
        return None
    func = node.func
    if isinstance(func, ast.Name):
        func_name = func.id
    elif isinstance(func, ast.Attribute):
        func_name = func.attr
    else:
        return None
    if func_name != "PluginMetadata":
        return None

    values: dict[str, Any] = {}
    positional = ["name", "description", "usage"]
    for index, arg in enumerate(node.args[:3]):
        try:
            values[positional[index]] = ast.literal_eval(arg)
        except (ValueError, TypeError):
            return None
    for keyword in node.keywords:
        if keyword.arg is None:
            continue
        try:
            values[keyword.arg] = ast.literal_eval(keyword.value)
        except (ValueError, TypeError):
            return None
    if "name" not in values or not isinstance(values["name"], str):
        return None
    requires = values.get("requires", frozenset())
    if requires is None:
        requires = frozenset()
    return PluginMetadata(
        name=values["name"],
        description=str(values.get("description", "")),
        usage=str(values.get("usage", "")),
        requires=frozenset(str(item) for item in requires),
    )

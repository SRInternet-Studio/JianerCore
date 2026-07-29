"""Dependency-aware plugin manager."""

from __future__ import annotations

import asyncio
import ast
import builtins
import importlib
import importlib.machinery
import importlib.util
import inspect
import sys
import threading
import time
import traceback
import uuid
import weakref
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

from .loader import DISABLED_PREFIX, PLUGIN_EXTENSIONS, PLUGIN_FOLDER, LoadResult
from .metadata import PLUGIN_NAME_PREFIX, Plugin, PluginMetadata, is_valid_plugin_name
from .runtime import (
    OwnedModule,
    PluginManagerState,
    ShutdownReport,
    plugin_runtime_scope,
)

BUILTIN_PLUGINS = {
    "jianerbot-plugin-alconna": "jianer.plugins.builtin.alconna",
    # Compatibility alias for plugins written before the canonical id was renamed.
    "jianer-alconna": "jianer.plugins.builtin.alconna",
}

_MODULE_OWNER_LOCK = threading.RLock()
_MODULE_OWNERS: dict[
    int,
    tuple[
        weakref.ReferenceType[ModuleType],
        weakref.ReferenceType["PluginManager"],
    ],
] = {}


@dataclass
class _PluginCandidate:
    key: str
    source: str | Path
    metadata: PluginMetadata | None
    display_name: str
    module_name: str | None = None
    plugin_root: Path | None = None

    @property
    def plugin_id(self) -> str | None:
        return self.metadata.name if self.metadata is not None else None


@dataclass
class _DisplacedModule:
    name: str
    module: ModuleType
    parent: ModuleType | None
    attribute: str | None


class PluginSetupError(RuntimeError):
    """Raised when a plugin's synchronous setup hook fails."""


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
        self.manager_id = uuid.uuid4().hex
        self.state = PluginManagerState.CREATED
        self.client: Any | None = None
        self._setup_plugins: set[str] = set()
        self._state_lock = threading.RLock()
        self._transition_lock = threading.RLock()
        self._inflight = 0
        self._drained = threading.Event()
        self._drained.set()
        self._shutdown_started = False
        self._shutdown_finished = threading.Event()
        self._shutdown_report: ShutdownReport | None = None
        self._shutdown_plugins: set[str] = set()
        self._owned_modules: dict[str, list[OwnedModule]] = {}
        self._displaced_modules: dict[str, list[_DisplacedModule]] = {}
        self._generation_namespaces: dict[str, str] = {}
        self._generation_modules: dict[str, dict[str, ModuleType]] = {}
        self._generation_sources: dict[str, dict[Path, bytes]] = {}
        self._entry_module_names: dict[str, str] = {}
        self._ever_activated = False

    def load_plugins(
        self,
        *plugin_folders: str | Path,
        create_missing: bool = True,
    ) -> LoadResult:
        """Discover and load all dependency-aware plugins in plugin folders."""

        with self._transition_lock:
            self._ensure_load_allowed()
            return self._load_plugins_locked(
                *plugin_folders,
                create_missing=create_missing,
            )

    def _load_plugins_locked(
        self,
        *plugin_folders: str | Path,
        create_missing: bool = True,
    ) -> LoadResult:
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

        with self._state_lock:
            if self.state == PluginManagerState.CREATED:
                self.state = PluginManagerState.LOADED
        self._fill_result(result)
        self._log_load_result(result)
        return result

    def load_plugin(self, plugin: str | Path) -> Plugin | None:
        """Load one plugin by built-in id, discovered id, module path, or file path."""

        with self._transition_lock:
            self._ensure_load_allowed()
            return self._load_plugin_locked(plugin)

    def _load_plugin_locked(self, plugin: str | Path) -> Plugin | None:
        candidate = self._resolve_candidate(plugin)
        if candidate is None:
            key = str(plugin)
            self._fail(f"{key} (plugin does not exist or is missing PluginMetadata)")
            return None
        plugin_id = candidate.plugin_id
        if plugin_id is None:
            self._warn(f"{candidate.display_name} (missing PluginMetadata; skipped)")
            return None
        loaded = self._load_candidate(plugin_id, candidate, [])
        if loaded is not None:
            with self._state_lock:
                if self.state == PluginManagerState.CREATED:
                    self.state = PluginManagerState.LOADED
        return loaded

    async def observe(self, event: Any, actions: Any) -> None:
        """Run every ``on_message_observe`` hook without handling the event."""

        if not self._acquire_dispatch():
            return
        try:
            await self._dispatch_hook(
                "on_message_observe", event, actions, stop_on_true=False
            )
        finally:
            self._release_dispatch()

    async def dispatch(
        self,
        event: Any,
        actions: Any,
        *,
        run_observers: bool = True,
    ) -> bool:
        """Dispatch normal message hooks, optionally running observers first."""

        if not self._acquire_dispatch():
            return False
        try:
            return await self._dispatch_with_acquired_lease(
                event,
                actions,
                run_observers=run_observers,
            )
        finally:
            self._release_dispatch()

    async def dispatch_fallback(self, event: Any, actions: Any) -> bool:
        """Dispatch fallback hooks after the host and normal plugins decline."""

        if not self._acquire_dispatch():
            return False
        try:
            return await self._dispatch_hook(
                "on_message_fallback", event, actions, stop_on_true=True
            )
        finally:
            self._release_dispatch()

    def setup_client(self, client: Any, *, activate: bool = True) -> None:
        """Run pending setup hooks and retain the Client used by this manager."""

        with self._transition_lock:
            self._setup_client_locked(client, activate=activate)

    def _setup_client_locked(self, client: Any, *, activate: bool) -> None:
        with self._state_lock:
            if self.state in {
                PluginManagerState.DRAINING,
                PluginManagerState.FAILED,
                PluginManagerState.CLOSED,
            }:
                raise RuntimeError(f"cannot set up manager in state {self.state.value}")
            if self.client is not None and self.client is not client:
                raise RuntimeError("PluginManager is already attached to another Client")
            self.client = client

        set_enabled = getattr(client, "_set_plugin_owner_enabled", None)
        if callable(set_enabled):
            set_enabled(self.manager_id, False)

        for plugin_id in list(self.dependency_order):
            plugin = self.plugins.get(plugin_id)
            if plugin is None:
                continue
            if not callable(getattr(plugin.module, "setup", None)):
                self._setup_plugins.add(plugin_id)

        try:
            for plugin_id in list(self.dependency_order):
                if plugin_id in self._setup_plugins:
                    continue
                plugin = self.plugins.get(plugin_id)
                if plugin is None:
                    continue
                setup = getattr(plugin.module, "setup", None)
                if callable(setup):
                    try:
                        with plugin_runtime_scope(self.manager_id, plugin_id):
                            response = setup(client, self)
                        if inspect.isawaitable(response):
                            close = getattr(response, "close", None)
                            if callable(close):
                                close()
                            raise TypeError(
                                f"{plugin_id} setup() must be synchronous"
                            )
                    finally:
                        candidate = self._candidates.get(plugin_id)
                        if candidate is not None:
                            self._record_owned_modules(plugin_id, candidate)
                self._setup_plugins.add(plugin_id)
        except Exception as exc:
            with self._state_lock:
                self.state = PluginManagerState.FAILED
            if callable(set_enabled):
                set_enabled(self.manager_id, False)
            remove_owner = getattr(client, "_remove_plugin_owner_subscriptions", None)
            if callable(remove_owner):
                remove_owner(self.manager_id)
            try:
                from .builtin import alconna

                alconna._remove_manager_matchers(self.manager_id)
            except Exception:
                self._log(
                    "error",
                    f"失败 Manager matcher 清理失败\n{traceback.format_exc()}",
                )
            raise PluginSetupError(
                f"plugin setup failed for {plugin_id}: {exc}"
            ) from exc

        with self._state_lock:
            if activate:
                self.state = PluginManagerState.ACTIVE
                self._ever_activated = True
            elif self.state != PluginManagerState.ACTIVE:
                self.state = PluginManagerState.STAGED
        if activate and callable(set_enabled):
            set_enabled(self.manager_id, True)
        if activate:
            self._displaced_modules.clear()

    def activate(self) -> None:
        """Make a staged manager and its setup subscriptions visible."""

        with self._transition_lock:
            self._activate_locked()

    def _activate_locked(self) -> None:
        with self._state_lock:
            if self.state == PluginManagerState.ACTIVE:
                return
            if self.state not in {PluginManagerState.LOADED, PluginManagerState.STAGED}:
                raise RuntimeError(f"cannot activate manager in state {self.state.value}")
            self._ensure_setup_complete_for_activation()
            self.state = PluginManagerState.ACTIVE
            self._ever_activated = True
        if self.client is not None:
            set_enabled = getattr(self.client, "_set_plugin_owner_enabled", None)
            if callable(set_enabled):
                set_enabled(self.manager_id, True)
        self._displaced_modules.clear()

    def _activate_for_swap_locked(self) -> None:
        """Transition an already locked staged manager during Client swap."""

        with self._state_lock:
            if self.state != PluginManagerState.STAGED:
                raise RuntimeError(
                    "only a staged PluginManager can be swapped into a Client"
                )
            self._ensure_setup_complete_for_activation()
            self.state = PluginManagerState.ACTIVE
            self._ever_activated = True
        self._displaced_modules.clear()

    def _begin_draining_for_swap_locked(self) -> None:
        """Transition an already locked manager without calling into Client."""

        with self._state_lock:
            if self.state != PluginManagerState.CLOSED:
                self.state = PluginManagerState.DRAINING

    def _begin_draining(self) -> None:
        with self._transition_lock:
            with self._state_lock:
                if self.state == PluginManagerState.CLOSED:
                    return
                self.state = PluginManagerState.DRAINING
            if self.client is not None:
                set_enabled = getattr(self.client, "_set_plugin_owner_enabled", None)
                if callable(set_enabled):
                    set_enabled(self.manager_id, False)

    async def shutdown(
        self,
        *,
        timeout: float | None = 30.0,
    ) -> ShutdownReport:
        """Drain callbacks, run reverse-order hooks, and unload owned runtime state."""

        with self._transition_lock:
            with self._state_lock:
                if self._shutdown_report is not None:
                    return self._shutdown_report
                if self._shutdown_started:
                    wait_for_other = True
                else:
                    self._shutdown_started = True
                    self._shutdown_finished.clear()
                    wait_for_other = False
            if not wait_for_other:
                self._begin_draining()

        if wait_for_other:
            await asyncio.to_thread(self._shutdown_finished.wait, timeout)
            with self._state_lock:
                if self._shutdown_report is not None:
                    return self._shutdown_report
            return ShutdownReport(
                manager_id=self.manager_id,
                completed=False,
                errors=("plugin manager shutdown timed out",),
            )

        try:
            errors: list[str] = []
            deadline = (
                None
                if timeout is None
                else time.monotonic() + max(timeout, 0.0)
            )

            drained = await asyncio.to_thread(
                self._drained.wait, _remaining_timeout(deadline)
            )
            if drained and self.client is not None:
                wait_owner = getattr(self.client, "_wait_plugin_owner_drained", None)
                if callable(wait_owner):
                    drained = await asyncio.to_thread(
                        wait_owner, self.manager_id, _remaining_timeout(deadline)
                    )

            if not drained:
                errors.append(
                    "plugin manager shutdown timed out while draining callbacks"
                )
                report = ShutdownReport(self.manager_id, False, tuple(errors))
                self._finish_shutdown(report, closed=False, final=False)
                return report

            for plugin_id in reversed(list(self.dependency_order)):
                if (
                    plugin_id not in self._setup_plugins
                    or plugin_id in self._shutdown_plugins
                ):
                    continue
                plugin = self.plugins.get(plugin_id)
                if plugin is None:
                    continue
                shutdown_hook = getattr(plugin.module, "shutdown", None)
                if not callable(shutdown_hook):
                    self._shutdown_plugins.add(plugin_id)
                    continue
                try:
                    with plugin_runtime_scope(self.manager_id, plugin_id):
                        response = shutdown_hook(self.client, self)
                        if inspect.isawaitable(response):
                            remaining = _remaining_timeout(deadline)
                            if remaining is None:
                                await response
                            else:
                                await asyncio.wait_for(response, timeout=remaining)
                    self._shutdown_plugins.add(plugin_id)
                except Exception as exc:
                    errors.append(f"{plugin_id} shutdown failed: {exc}")
                    self._log(
                        "error",
                        f"插件关闭失败：{plugin_id}\n{traceback.format_exc()}",
                    )

            if self.client is not None:
                remove_owner = getattr(
                    self.client, "_remove_plugin_owner_subscriptions", None
                )
                if callable(remove_owner):
                    remove_owner(self.manager_id)

            try:
                from .builtin import alconna

                alconna._remove_manager_matchers(self.manager_id)
            except Exception as exc:
                errors.append(f"failed to clear Alconna matchers: {exc}")

            try:
                self._cleanup_owned_modules(
                    restore_displaced=not self._ever_activated
                )
            except Exception as exc:
                errors.append(f"failed to clean plugin modules: {exc}")
                self._log("error", f"插件模块清理失败\n{traceback.format_exc()}")

            report = ShutdownReport(
                manager_id=self.manager_id,
                completed=not errors,
                errors=tuple(errors),
            )
            self._finish_shutdown(report, closed=True, final=True)
            return report
        except BaseException:
            self._abort_shutdown()
            raise

    async def _dispatch_hook(
        self,
        hook_name: str,
        event: Any,
        actions: Any,
        *,
        stop_on_true: bool,
    ) -> bool:
        handled = False
        for plugin_id in list(self.dependency_order):
            plugin = self.plugins.get(plugin_id)
            if plugin is None:
                continue
            dispatcher = getattr(plugin.module, hook_name, None)
            if not callable(dispatcher):
                continue
            try:
                with plugin_runtime_scope(self.manager_id, plugin_id):
                    response = dispatcher(event, actions)
                    if inspect.isawaitable(response):
                        response = await response
                if stop_on_true and response is True:
                    handled = True
                    break
            except Exception:
                self._log(
                    "error",
                    f"插件派发失败：{plugin_id} ({hook_name})\n"
                    f"{traceback.format_exc()}",
                )
        return handled

    async def _dispatch_with_acquired_lease(
        self,
        event: Any,
        actions: Any,
        *,
        run_observers: bool = True,
    ) -> bool:
        """Dispatch after the caller atomically acquired this manager's lease."""

        if run_observers:
            await self._dispatch_hook(
                "on_message_observe", event, actions, stop_on_true=False
            )
        return await self._dispatch_hook(
            "on_message", event, actions, stop_on_true=True
        )

    def _acquire_dispatch(self) -> bool:
        with self._state_lock:
            if self.state not in {
                PluginManagerState.CREATED,
                PluginManagerState.LOADED,
                PluginManagerState.ACTIVE,
            }:
                return False
            self._inflight += 1
            self._drained.clear()
            return True

    def _release_dispatch(self) -> None:
        with self._state_lock:
            self._inflight = max(0, self._inflight - 1)
            if self._inflight == 0:
                self._drained.set()

    def _finish_shutdown(
        self,
        report: ShutdownReport,
        *,
        closed: bool,
        final: bool,
    ) -> None:
        with self._state_lock:
            if closed:
                self.state = PluginManagerState.CLOSED
            if final:
                self._shutdown_report = report
            else:
                self._shutdown_started = False
            self._shutdown_finished.set()

    def _abort_shutdown(self) -> None:
        """Release shutdown single-flight state after cancellation or failure."""

        with self._state_lock:
            if self._shutdown_report is None:
                self._shutdown_started = False
            self._shutdown_finished.set()

    def _ensure_load_allowed(self) -> None:
        with self._state_lock:
            if self.state == PluginManagerState.ACTIVE:
                raise RuntimeError(
                    "cannot incrementally load an active PluginManager; "
                    "stage and swap a new manager"
                )
            if self.state in {
                PluginManagerState.DRAINING,
                PluginManagerState.FAILED,
                PluginManagerState.CLOSED,
            }:
                raise RuntimeError(
                    f"cannot load plugins in state {self.state.value}"
                )

    def _ensure_setup_complete_for_activation(self) -> None:
        pending = [
            plugin_id
            for plugin_id in self.dependency_order
            if plugin_id not in self._setup_plugins
        ]
        if pending:
            raise RuntimeError(
                "cannot activate PluginManager with pending setup hooks: "
                + ", ".join(pending)
            )

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
            return _PluginCandidate(
                key=str(entry.resolve()),
                source=entry_file,
                metadata=metadata,
                display_name=entry.name,
                plugin_root=entry.resolve(),
            )
        if entry.suffix in PLUGIN_EXTENSIONS:
            metadata = _read_plugin_metadata(entry)
            return _PluginCandidate(
                key=str(entry.resolve()),
                source=entry,
                metadata=metadata,
                display_name=entry.stem,
                plugin_root=entry.resolve(),
            )
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
            return _PluginCandidate(
                key=plugin_key,
                source=loaded.source,
                metadata=loaded.metadata,
                display_name=plugin_key,
            )
        if plugin_key in self._candidates:
            return self._candidates[plugin_key]
        if plugin_key in self.builtin_plugins:
            module_name = self.builtin_plugins[plugin_key]
            metadata = _read_module_metadata(module_name)
            if metadata is None:
                metadata = PluginMetadata(name=plugin_key)
            candidate = _PluginCandidate(
                key=plugin_key,
                source=module_name,
                metadata=metadata,
                display_name=plugin_key,
                module_name=module_name,
            )
            self._add_candidate(candidate)
            return candidate

        metadata = _read_module_metadata(plugin_key)
        if metadata is None:
            return None
        candidate = _PluginCandidate(
            key=plugin_key,
            source=plugin_key,
            metadata=metadata,
            display_name=metadata.name,
            module_name=plugin_key,
        )
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

        module = self._import_candidate(plugin_id, candidate)
        self._loading.discard(plugin_id)
        if module is None:
            return None

        runtime_metadata = getattr(module, "__plugin_meta__", metadata)
        if not isinstance(runtime_metadata, PluginMetadata):
            self._fail(f"{plugin_id} (__plugin_meta__ must be PluginMetadata)")
            self._discard_plugin_import(plugin_id, restore_displaced=True)
            return None
        if not is_valid_plugin_name(runtime_metadata.name):
            self._fail(f"{runtime_metadata.name} (invalid plugin ID: use {PLUGIN_NAME_PREFIX}{{name}})")
            self._discard_plugin_import(plugin_id, restore_displaced=True)
            return None
        if runtime_metadata.name != plugin_id:
            self._fail(f"{plugin_id} (plugin ID mismatch: {runtime_metadata.name})")
            self._discard_plugin_import(plugin_id, restore_displaced=True)
            return None
        if plugin_id in self.plugins:
            self._fail(f"{plugin_id} (duplicate plugin ID)")
            self._discard_plugin_import(plugin_id, restore_displaced=True)
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
        if not callable(getattr(module, "setup", None)):
            self._setup_plugins.add(plugin_id)
        self._log("info", f"已加载插件：{plugin_id}")
        return loaded

    def _import_candidate(
        self,
        plugin_id: str,
        candidate: _PluginCandidate,
    ) -> ModuleType | None:
        unique_module_name: str | None = None
        try:
            if candidate.module_name is not None:
                with plugin_runtime_scope(self.manager_id, plugin_id):
                    return importlib.import_module(candidate.module_name)

            path = Path(candidate.source)
            self._snapshot_generation_sources(plugin_id, candidate)
            unique_module_name = f"jianer_user_plugin_{candidate.display_name}_{uuid.uuid4().hex}"
            spec = importlib.util.spec_from_file_location(unique_module_name, path)
            if spec is None or spec.loader is None:
                self._fail(f"{candidate.display_name} (entry error: unable to create import spec)")
                self._restore_displaced_modules(plugin_id)
                return None
            module = importlib.util.module_from_spec(spec)
            module.__dict__["__builtins__"] = self._plugin_builtins(
                plugin_id,
                candidate,
            )
            self._entry_module_names[plugin_id] = unique_module_name
            sys.modules[unique_module_name] = module
            with plugin_runtime_scope(self.manager_id, plugin_id):
                spec.loader.exec_module(module)
            self._record_owned_modules(plugin_id, candidate)
            return module
        except Exception as exc:
            self._fail(f"{candidate.display_name} (import error: {exc})")
            self._record_owned_modules(plugin_id, candidate)
            if unique_module_name is not None:
                module = sys.modules.get(unique_module_name)
                if module is not None:
                    self._remove_module_identity(unique_module_name, module)
            self._discard_plugin_import(plugin_id, restore_displaced=True)
            self._log("error", f"插件导入失败：{candidate.display_name}\n{traceback.format_exc()}")
            return None

    def _snapshot_generation_sources(
        self,
        plugin_id: str,
        candidate: _PluginCandidate,
    ) -> None:
        root = candidate.plugin_root
        if root is None or not root.is_dir():
            return
        sources: dict[Path, bytes] = {}
        for path in root.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            try:
                sources[path.relative_to(root)] = path.read_bytes()
            except OSError:
                continue
        self._generation_sources[plugin_id] = sources

    def _plugin_builtins(
        self,
        plugin_id: str,
        candidate: _PluginCandidate,
    ) -> dict[str, Any]:
        namespace = dict(vars(builtins))
        original_import = builtins.__import__

        def generation_import(
            name: str,
            globals_: dict[str, Any] | None = None,
            locals_: dict[str, Any] | None = None,
            fromlist: tuple[str, ...] | list[str] = (),
            level: int = 0,
        ) -> Any:
            prefix = _plugin_import_prefix(candidate)
            if (
                level != 0
                or prefix is None
                or not (
                    name == prefix
                    or name.startswith(f"{prefix}.")
                    or prefix.startswith(f"{name}.")
                )
            ):
                return original_import(
                    name,
                    globals_,
                    locals_,
                    fromlist,
                    level,
                )
            with _MODULE_OWNER_LOCK:
                target_name = (
                    prefix if prefix.startswith(f"{name}.") else name
                )
                target = self._load_generation_module(
                    plugin_id,
                    candidate,
                    target_name,
                )
                if name != target_name:
                    target = self._generation_facade(
                        plugin_id,
                        target_name,
                    )
                elif fromlist:
                    for item in fromlist:
                        if item == "*" or hasattr(target, item):
                            continue
                        child_name = f"{name}.{item}"
                        if self._generation_source_exists(
                            plugin_id,
                            candidate,
                            child_name,
                        ):
                            self._load_generation_module(
                                plugin_id,
                                candidate,
                                child_name,
                            )
                if fromlist:
                    return target
                return self._generation_facade(plugin_id, target_name)

        namespace["__import__"] = generation_import
        return namespace

    def _load_generation_module(
        self,
        plugin_id: str,
        candidate: _PluginCandidate,
        canonical_name: str,
    ) -> ModuleType:
        prefix = _plugin_import_prefix(candidate)
        root = candidate.plugin_root
        if prefix is None or root is None or not root.is_dir():
            raise ImportError(
                f"{canonical_name} is outside the plugin generation namespace"
            )
        if not (
            canonical_name == prefix
            or canonical_name.startswith(f"{prefix}.")
            or prefix.startswith(f"{canonical_name}.")
        ):
            raise ImportError(
                f"{canonical_name} is outside plugin package {prefix}"
            )

        namespace = self._generation_namespaces.setdefault(
            plugin_id,
            f"_jianer_plugin_generation_{self.manager_id}_{uuid.uuid4().hex}",
        )
        modules = self._generation_modules.setdefault(plugin_id, {})
        self._ensure_generation_namespace(plugin_id, namespace)

        prefix_parts = prefix.split(".")
        package_paths = [root.parent, root]
        for index, part in enumerate(prefix_parts):
            qualified = f"{namespace}.{'.'.join(prefix_parts[: index + 1])}"
            package_path = (
                package_paths[index]
                if index < len(package_paths)
                else root.joinpath(*prefix_parts[2 : index + 1])
            )
            self._ensure_generation_package(
                plugin_id,
                candidate,
                qualified,
                package_path,
            )

        if prefix.startswith(f"{canonical_name}."):
            qualified = f"{namespace}.{canonical_name}"
            module = modules.get(qualified)
            if module is None:
                raise ImportError(f"unable to load package {canonical_name}")
            return module

        suffix = canonical_name[len(prefix) :].lstrip(".")
        current_path = root
        current_canonical = prefix
        if suffix:
            for part in suffix.split("."):
                current_canonical = f"{current_canonical}.{part}"
                qualified = f"{namespace}.{current_canonical}"
                existing = modules.get(qualified)
                if existing is not None:
                    current_path = self._generation_child_path(
                        plugin_id,
                        candidate,
                        current_path,
                        part,
                    )
                    continue
                package_path = current_path / part
                module_path = current_path / f"{part}.py"
                if self._generation_directory_exists(
                    plugin_id,
                    candidate,
                    package_path,
                ):
                    self._ensure_generation_package(
                        plugin_id,
                        candidate,
                        qualified,
                        package_path,
                    )
                    current_path = package_path
                    continue
                if self._generation_source(
                    plugin_id,
                    candidate,
                    module_path,
                ) is None:
                    raise ModuleNotFoundError(
                        f"No module named {current_canonical!r}"
                    )
                self._load_generation_file(
                    plugin_id,
                    candidate,
                    qualified,
                    module_path,
                )
                current_path = module_path

        qualified_name = f"{namespace}.{canonical_name}"
        module = modules.get(qualified_name)
        if module is None:
            raise ImportError(f"unable to load module {canonical_name}")
        return module

    def _ensure_generation_namespace(
        self,
        plugin_id: str,
        namespace: str,
    ) -> ModuleType:
        modules = self._generation_modules.setdefault(plugin_id, {})
        existing = modules.get(namespace)
        if existing is not None:
            return existing
        module = ModuleType(namespace)
        module.__package__ = namespace
        module.__path__ = []
        module.__spec__ = importlib.machinery.ModuleSpec(
            namespace,
            loader=None,
            is_package=True,
        )
        sys.modules[namespace] = module
        modules[namespace] = module
        return module

    def _ensure_generation_package(
        self,
        plugin_id: str,
        candidate: _PluginCandidate,
        qualified_name: str,
        package_path: Path,
    ) -> ModuleType:
        modules = self._generation_modules.setdefault(plugin_id, {})
        existing = modules.get(qualified_name)
        if existing is not None:
            return existing
        init_file = package_path / "__init__.py"
        source = self._generation_source(
            plugin_id,
            candidate,
            init_file,
        )
        module = ModuleType(qualified_name)
        module.__package__ = qualified_name
        module.__path__ = [str(package_path)]
        module.__spec__ = importlib.machinery.ModuleSpec(
            qualified_name,
            loader=None,
            is_package=True,
        )
        if source is not None:
            module.__file__ = str(init_file)
            module.__dict__["__builtins__"] = self._plugin_builtins(
                plugin_id,
                candidate,
            )
        self._register_generation_module(plugin_id, qualified_name, module)
        if source is not None:
            try:
                with plugin_runtime_scope(self.manager_id, plugin_id):
                    exec(
                        compile(source, str(init_file), "exec"),
                        module.__dict__,
                    )
            except BaseException:
                self._remove_generation_module(
                    plugin_id,
                    qualified_name,
                    module,
                )
                raise
        return module

    def _load_generation_file(
        self,
        plugin_id: str,
        candidate: _PluginCandidate,
        qualified_name: str,
        module_path: Path,
    ) -> ModuleType:
        source = self._generation_source(
            plugin_id,
            candidate,
            module_path,
        )
        if source is None:
            raise ImportError(f"unable to load module {qualified_name}")
        module = ModuleType(qualified_name)
        module.__file__ = str(module_path)
        module.__package__ = qualified_name.rpartition(".")[0]
        module.__spec__ = importlib.machinery.ModuleSpec(
            qualified_name,
            loader=None,
            is_package=False,
        )
        module.__dict__["__builtins__"] = self._plugin_builtins(
            plugin_id,
            candidate,
        )
        self._register_generation_module(plugin_id, qualified_name, module)
        try:
            with plugin_runtime_scope(self.manager_id, plugin_id):
                exec(
                    compile(source, str(module_path), "exec"),
                    module.__dict__,
                )
        except BaseException:
            self._remove_generation_module(
                plugin_id,
                qualified_name,
                module,
            )
            raise
        return module

    def _register_generation_module(
        self,
        plugin_id: str,
        name: str,
        module: ModuleType,
    ) -> None:
        self._generation_modules.setdefault(plugin_id, {})[name] = module
        sys.modules[name] = module
        parent_name, separator, attribute = name.rpartition(".")
        if separator:
            parent = sys.modules.get(parent_name)
            if isinstance(parent, ModuleType):
                setattr(parent, attribute, module)

    def _remove_generation_module(
        self,
        plugin_id: str,
        name: str,
        module: ModuleType,
    ) -> None:
        self._remove_module_identity(name, module)
        self._generation_modules.get(plugin_id, {}).pop(name, None)

    def _generation_facade(
        self,
        plugin_id: str,
        canonical_name: str,
    ) -> ModuleType:
        namespace = self._generation_namespaces[plugin_id]
        parts = canonical_name.split(".")
        root: ModuleType | None = None
        parent: ModuleType | None = None
        for index, part in enumerate(parts):
            canonical_part = ".".join(parts[: index + 1])
            actual = self._generation_modules[plugin_id].get(
                f"{namespace}.{canonical_part}"
            )
            if actual is None:
                child = ModuleType(canonical_part)
            elif index == len(parts) - 1:
                child = actual
            else:
                child = ModuleType(canonical_part)
                child.__dict__.update(actual.__dict__)
                child.__name__ = canonical_part
            if root is None:
                root = child
            if parent is not None:
                setattr(parent, part, child)
            parent = child
        if root is None:
            raise ImportError(f"unable to build import facade for {canonical_name}")
        return root

    def _generation_source_exists(
        self,
        plugin_id: str,
        candidate: _PluginCandidate,
        canonical_name: str,
    ) -> bool:
        prefix = _plugin_import_prefix(candidate)
        root = candidate.plugin_root
        if prefix is None or root is None:
            return False
        if canonical_name == prefix:
            return True
        if not canonical_name.startswith(f"{prefix}."):
            return False
        relative = canonical_name[len(prefix) :].lstrip(".").split(".")
        path = root.joinpath(*relative)
        return (
            self._generation_directory_exists(
                plugin_id,
                candidate,
                path,
            )
            or self._generation_source(
                plugin_id,
                candidate,
                path.with_suffix(".py"),
            )
            is not None
        )

    def _generation_source(
        self,
        plugin_id: str,
        candidate: _PluginCandidate,
        path: Path,
    ) -> bytes | None:
        root = candidate.plugin_root
        if root is None:
            return None
        try:
            relative = path.relative_to(root)
        except ValueError:
            return None
        return self._generation_sources.get(plugin_id, {}).get(relative)

    def _generation_directory_exists(
        self,
        plugin_id: str,
        candidate: _PluginCandidate,
        path: Path,
    ) -> bool:
        root = candidate.plugin_root
        if root is None:
            return False
        if path == root:
            return True
        try:
            relative = path.relative_to(root)
        except ValueError:
            return path == root.parent
        prefix = relative.parts
        return any(
            source.parts[: len(prefix)] == prefix
            for source in self._generation_sources.get(plugin_id, {})
        )

    def _generation_child_path(
        self,
        plugin_id: str,
        candidate: _PluginCandidate,
        parent: Path,
        child: str,
    ) -> Path:
        package_path = parent / child
        if self._generation_directory_exists(
            plugin_id,
            candidate,
            package_path,
        ):
            return package_path
        return parent / f"{child}.py"

    def _evict_cached_plugin_modules(
        self,
        plugin_id: str,
        candidate: _PluginCandidate,
    ) -> None:
        root = candidate.plugin_root
        if root is None:
            return
        displaced: list[_DisplacedModule] = []
        modules = sorted(
            list(sys.modules.items()),
            key=lambda item: item[0].count("."),
            reverse=True,
        )
        for name, module in modules:
            if not isinstance(module, ModuleType):
                continue
            if not _module_is_within_root(module, root):
                continue
            parent, attribute = _module_parent_attribute(name, module)
            if sys.modules.get(name) is module:
                sys.modules.pop(name, None)
            if parent is not None and attribute is not None:
                try:
                    delattr(parent, attribute)
                except AttributeError:
                    pass
            displaced.append(
                _DisplacedModule(
                    name=name,
                    module=module,
                    parent=parent,
                    attribute=attribute,
                )
            )
        if displaced:
            self._displaced_modules[plugin_id] = displaced

    def _record_owned_modules(
        self,
        plugin_id: str,
        candidate: _PluginCandidate,
    ) -> None:
        root = candidate.plugin_root
        if root is None:
            return
        owned = list(self._owned_modules.get(plugin_id, ()))
        seen = {(item.name, id(item.module)) for item in owned}
        generation_namespace = self._generation_namespaces.get(plugin_id)
        entry_module_name = self._entry_module_names.get(plugin_id)
        for name, module in list(sys.modules.items()):
            if not isinstance(module, ModuleType):
                continue
            if not (
                name == entry_module_name
                or (
                    generation_namespace is not None
                    and (
                        name == generation_namespace
                        or name.startswith(f"{generation_namespace}.")
                    )
                )
            ):
                continue
            module_file = _resolved_module_file(module)
            if module_file is None or not _path_is_within_root(module_file, root):
                continue
            key = (name, id(module))
            if key in seen:
                continue
            seen.add(key)
            owned.append(
                OwnedModule(
                    name=name,
                    module=module,
                    file=str(module_file),
                    plugin_root=str(root),
                )
            )
            with _MODULE_OWNER_LOCK:
                _MODULE_OWNERS[id(module)] = (
                    weakref.ref(module),
                    weakref.ref(self),
                )
        self._owned_modules[plugin_id] = owned

    def _discard_plugin_import(
        self,
        plugin_id: str,
        *,
        restore_displaced: bool,
    ) -> None:
        try:
            from .builtin import alconna

            alconna._remove_plugin_matchers(self.manager_id, plugin_id)
        except Exception:
            self._log(
                "error",
                f"插件 matcher 清理失败：{plugin_id}\n{traceback.format_exc()}",
            )
        for owned in reversed(self._owned_modules.pop(plugin_id, [])):
            self._remove_module_identity(owned.name, owned.module)
        for name, module in sorted(
            self._generation_modules.pop(plugin_id, {}).items(),
            key=lambda item: item[0].count("."),
            reverse=True,
        ):
            self._remove_module_identity(name, module)
        self._generation_namespaces.pop(plugin_id, None)
        self._generation_sources.pop(plugin_id, None)
        self._entry_module_names.pop(plugin_id, None)
        if restore_displaced:
            self._restore_displaced_modules(plugin_id)
        else:
            self._displaced_modules.pop(plugin_id, None)

    def _cleanup_owned_modules(self, *, restore_displaced: bool) -> None:
        for plugin_id in reversed(list(self._owned_modules)):
            self._discard_plugin_import(
                plugin_id,
                restore_displaced=restore_displaced,
            )
        if restore_displaced:
            for plugin_id in reversed(list(self._displaced_modules)):
                self._restore_displaced_modules(plugin_id)
        else:
            self._displaced_modules.clear()

    def _restore_displaced_modules(self, plugin_id: str) -> None:
        displaced = self._displaced_modules.pop(plugin_id, [])
        for record in reversed(displaced):
            if not _module_owner_allows_restore(record.module):
                continue
            current = sys.modules.get(record.name)
            if current is None:
                sys.modules[record.name] = record.module
            elif current is not record.module:
                continue
            if record.parent is not None and record.attribute is not None:
                current_attribute = getattr(
                    record.parent, record.attribute, None
                )
                if current_attribute is None or current_attribute is record.module:
                    setattr(record.parent, record.attribute, record.module)

    @staticmethod
    def _remove_module_identity(name: str, module: ModuleType) -> None:
        if sys.modules.get(name) is module:
            sys.modules.pop(name, None)
        parent, attribute = _module_parent_attribute(name, module)
        if parent is not None and attribute is not None:
            try:
                delattr(parent, attribute)
            except AttributeError:
                pass

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


def _remaining_timeout(deadline: float | None) -> float | None:
    if deadline is None:
        return None
    return max(0.0, deadline - time.monotonic())


def _plugin_import_prefix(candidate: _PluginCandidate) -> str | None:
    root = candidate.plugin_root
    if root is None or not root.is_dir():
        return None
    return f"{root.parent.name}.{root.name}"


def _resolved_module_file(module: ModuleType) -> Path | None:
    module_file = getattr(module, "__file__", None)
    if not module_file:
        return None
    try:
        return Path(module_file).resolve()
    except (OSError, RuntimeError, TypeError):
        return None


def _path_is_within_root(path: Path, root: Path) -> bool:
    try:
        resolved_root = root.resolve()
        if resolved_root.is_file():
            return path == resolved_root
        path.relative_to(resolved_root)
        return True
    except (OSError, RuntimeError, ValueError):
        return False


def _module_is_within_root(module: ModuleType, root: Path) -> bool:
    module_file = _resolved_module_file(module)
    return (
        module_file is not None
        and _path_is_within_root(module_file, root)
    )


def _module_parent_attribute(
    name: str,
    module: ModuleType,
) -> tuple[ModuleType | None, str | None]:
    parent_name, separator, attribute = name.rpartition(".")
    if not separator:
        return None, None
    parent = sys.modules.get(parent_name)
    if not isinstance(parent, ModuleType):
        return None, None
    if getattr(parent, attribute, None) is not module:
        return None, None
    return parent, attribute


def _module_owner_allows_restore(module: ModuleType) -> bool:
    with _MODULE_OWNER_LOCK:
        ownership = _MODULE_OWNERS.get(id(module))
        if ownership is None:
            return True
        module_ref, manager_ref = ownership
        if module_ref() is not module:
            _MODULE_OWNERS.pop(id(module), None)
            return True
        manager = manager_ref()
        if manager is None:
            return False
        return manager.state in {
            PluginManagerState.CREATED,
            PluginManagerState.LOADED,
            PluginManagerState.STAGED,
            PluginManagerState.ACTIVE,
        }


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

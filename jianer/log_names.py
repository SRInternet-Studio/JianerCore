"""Stable display names for runtime-isolated plugin modules."""

from __future__ import annotations

import threading
from typing import Any, MutableMapping


_MODULE_LOG_NAMES: dict[str, str] = {}
_MODULE_LOG_PREFIXES: dict[str, str] = {}
_MODULE_LOG_NAMES_LOCK = threading.RLock()


def register_module_log_name(runtime_name: str, display_name: str) -> None:
    """Associate an internal runtime module with its public log name."""

    with _MODULE_LOG_NAMES_LOCK:
        _MODULE_LOG_NAMES[runtime_name] = display_name


def unregister_module_log_name(runtime_name: str) -> None:
    """Remove a runtime module's public log-name association."""

    with _MODULE_LOG_NAMES_LOCK:
        _MODULE_LOG_NAMES.pop(runtime_name, None)


def register_module_log_prefix(runtime_prefix: str, display_prefix: str) -> None:
    """Associate every child of a runtime package with a public log prefix."""

    with _MODULE_LOG_NAMES_LOCK:
        _MODULE_LOG_PREFIXES[runtime_prefix] = display_prefix


def unregister_module_log_prefix(runtime_prefix: str) -> None:
    """Remove a runtime package's public log-prefix association."""

    with _MODULE_LOG_NAMES_LOCK:
        _MODULE_LOG_PREFIXES.pop(runtime_prefix, None)


def patch_log_record(record: MutableMapping[str, Any]) -> None:
    """Replace an isolated module name before Loguru formats a record."""

    runtime_name = record.get("name")
    if not isinstance(runtime_name, str):
        return
    with _MODULE_LOG_NAMES_LOCK:
        display_name = _MODULE_LOG_NAMES.get(runtime_name)
        if display_name is None:
            matching_prefix = max(
                (
                    prefix
                    for prefix in _MODULE_LOG_PREFIXES
                    if runtime_name == prefix
                    or runtime_name.startswith(f"{prefix}.")
                ),
                key=len,
                default=None,
            )
            if matching_prefix is not None:
                display_name = (
                    _MODULE_LOG_PREFIXES[matching_prefix]
                    + runtime_name[len(matching_prefix) :]
                )
    if display_name is not None:
        record["name"] = display_name

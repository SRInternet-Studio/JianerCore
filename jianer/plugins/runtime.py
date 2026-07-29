"""Runtime ownership and lifecycle types for Jianer plugins."""

from __future__ import annotations

import contextlib
import contextvars
from dataclasses import dataclass
from enum import Enum
from types import ModuleType
from typing import Iterator, Optional, Tuple, Type


class PluginManagerState(str, Enum):
    """Lifecycle states for a :class:`PluginManager`."""

    CREATED = "created"
    LOADED = "loaded"
    STAGED = "staged"
    ACTIVE = "active"
    DRAINING = "draining"
    FAILED = "failed"
    CLOSED = "closed"


@dataclass(frozen=True)
class SubscriptionOwner:
    """Identify the manager and plugin that own a Client subscription."""

    manager_id: str
    plugin_id: str


@dataclass(frozen=True)
class SubscriptionToken:
    """Opaque handle returned by ``Client.subscribe``."""

    token: str
    event_type: Type[object]


@dataclass(frozen=True)
class ShutdownReport:
    """Result of shutting down a plugin manager or Client."""

    manager_id: str
    completed: bool
    errors: Tuple[str, ...] = ()


@dataclass(frozen=True)
class OwnedModule:
    """A concrete module object loaded from a plugin's filesystem root."""

    name: str
    module: ModuleType
    file: str
    plugin_root: str


_CURRENT_PLUGIN_OWNER: contextvars.ContextVar[Optional[SubscriptionOwner]] = (
    contextvars.ContextVar("jianer_current_plugin_owner", default=None)
)


def current_plugin_owner() -> Optional[SubscriptionOwner]:
    """Return the plugin currently being imported or invoked, if any."""

    return _CURRENT_PLUGIN_OWNER.get()


@contextlib.contextmanager
def plugin_runtime_scope(manager_id: str, plugin_id: str) -> Iterator[SubscriptionOwner]:
    """Associate nested imports, decorators, and subscriptions with one plugin."""

    owner = SubscriptionOwner(manager_id=manager_id, plugin_id=plugin_id)
    token = _CURRENT_PLUGIN_OWNER.set(owner)
    try:
        yield owner
    finally:
        _CURRENT_PLUGIN_OWNER.reset(token)


__all__ = [
    "OwnedModule",
    "PluginManagerState",
    "ShutdownReport",
    "SubscriptionOwner",
    "SubscriptionToken",
    "current_plugin_owner",
]

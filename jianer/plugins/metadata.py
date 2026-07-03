"""Plugin metadata models."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from types import ModuleType
from typing import Any

PLUGIN_NAME_PREFIX = "jianerbot-plugin-"
PLUGIN_NAME_PATTERN = re.compile(r"^jianerbot-plugin-[a-z0-9]+(?:-[a-z0-9]+)*$")
RESERVED_PLUGIN_NAMES = frozenset()


def is_valid_plugin_name(name: str) -> bool:
    """Return whether a new-style plugin id follows the JianerBot convention."""

    return name in RESERVED_PLUGIN_NAMES or bool(PLUGIN_NAME_PATTERN.fullmatch(name))


@dataclass(frozen=True)
class PluginMetadata:
    """Metadata used by the dependency-aware plugin system."""

    name: str
    description: str = ""
    usage: str = ""
    requires: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        object.__setattr__(self, "requires", frozenset(self.requires))


@dataclass
class Plugin:
    """Loaded plugin record."""

    name: str
    module: ModuleType
    metadata: PluginMetadata
    source: str
    dependencies: frozenset[str] = field(default_factory=frozenset)
    extra: dict[str, Any] = field(default_factory=dict)

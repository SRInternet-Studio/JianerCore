"""Plugin metadata models."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import ModuleType
from typing import Any


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

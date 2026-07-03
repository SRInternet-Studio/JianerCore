"""Plugin loading helpers for JianerCore."""

from .loader import (
    DISABLED_PREFIX,
    INCOMPATIBLE_IN_FEISHU,
    PLUGIN_EXTENSIONS,
    PLUGIN_FOLDER,
    LoadResult,
    load_plugins,
)
from .manager import BUILTIN_PLUGINS, PluginManager
from .metadata import Plugin, PluginMetadata

__all__ = [
    "DISABLED_PREFIX",
    "INCOMPATIBLE_IN_FEISHU",
    "PLUGIN_EXTENSIONS",
    "PLUGIN_FOLDER",
    "BUILTIN_PLUGINS",
    "LoadResult",
    "Plugin",
    "PluginManager",
    "PluginMetadata",
    "load_plugins",
]

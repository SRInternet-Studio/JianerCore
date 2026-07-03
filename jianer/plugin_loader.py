"""Compatibility exports for the JianerCore plugin loader."""

from .plugins import (
    BUILTIN_PLUGINS,
    DISABLED_PREFIX,
    INCOMPATIBLE_IN_FEISHU,
    PLUGIN_EXTENSIONS,
    PLUGIN_FOLDER,
    LoadResult,
    Plugin,
    PluginManager,
    PluginMetadata,
    load_plugins,
)

__all__ = [
    "BUILTIN_PLUGINS",
    "DISABLED_PREFIX",
    "INCOMPATIBLE_IN_FEISHU",
    "PLUGIN_EXTENSIONS",
    "PLUGIN_FOLDER",
    "LoadResult",
    "Plugin",
    "PluginManager",
    "PluginMetadata",
    "load_plugins",
]

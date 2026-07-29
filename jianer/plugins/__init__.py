"""Plugin loading helpers for JianerCore."""

from .loader import (
    DISABLED_PREFIX,
    INCOMPATIBLE_IN_FEISHU,
    PLUGIN_EXTENSIONS,
    PLUGIN_FOLDER,
    LoadResult,
    load_plugins,
)
from .manager import BUILTIN_PLUGINS, PluginManager, PluginSetupError
from .metadata import (
    PLUGIN_NAME_PATTERN,
    PLUGIN_NAME_PREFIX,
    RESERVED_PLUGIN_NAMES,
    Plugin,
    PluginMetadata,
    is_valid_plugin_name,
)
from .runtime import (
    PluginManagerState,
    ShutdownReport,
    SubscriptionOwner,
    SubscriptionToken,
)

__all__ = [
    "DISABLED_PREFIX",
    "INCOMPATIBLE_IN_FEISHU",
    "PLUGIN_EXTENSIONS",
    "PLUGIN_NAME_PATTERN",
    "PLUGIN_NAME_PREFIX",
    "PLUGIN_FOLDER",
    "RESERVED_PLUGIN_NAMES",
    "BUILTIN_PLUGINS",
    "LoadResult",
    "Plugin",
    "PluginManager",
    "PluginManagerState",
    "PluginMetadata",
    "PluginSetupError",
    "ShutdownReport",
    "SubscriptionOwner",
    "SubscriptionToken",
    "is_valid_plugin_name",
    "load_plugins",
]

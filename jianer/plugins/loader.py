"""Plugin loader for JianerCore-compatible plugin directories.

Plugin contract:
- Put enabled plugin files under ``plugins/`` as ``name.py`` or ``name.pyw``.
- Or put a plugin directory under ``plugins/name/`` with an entry file named
  ``setup.py``.
- Prefix a plugin file or directory with ``d_`` to mark it as disabled.
- A valid plugin module must export ``TRIGGHT_KEYWORD: str`` and
  ``async def on_message(...): ...``.
- ``HELP_MESSAGE: str`` is optional and will be collected into
  ``LoadResult.help_text``.

The misspelled name ``TRIGGHT_KEYWORD`` is kept for compatibility with the
existing plugin ecosystem.
"""

from __future__ import annotations

import importlib.util
import logging
import sys
import traceback
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any, Iterable

PLUGIN_FOLDER = "plugins"
DISABLED_PREFIX = "d_"
PLUGIN_EXTENSIONS = (".py", ".pyw")

INCOMPATIBLE_IN_FEISHU = frozenset(
    {
        "CheckAccount",
        "CheckGroup",
        "LikePlugin",
        "AdvancedQuote",
        "SumUp_MySQL",
    }
)


@dataclass
class LoadResult:
    """Collected state from one plugin loading pass."""

    plugins: list[ModuleType] = field(default_factory=list)
    loaded: list[str] = field(default_factory=list)
    loaded_display: list[str] = field(default_factory=list)
    disabled: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    _help_lines: list[str] = field(default_factory=list)

    @property
    def help_text(self) -> str:
        return "".join(f"\n       {line}" for line in self._help_lines)


def load_plugins(
    config: Any = None,
    logger: logging.Logger | None = None,
    *,
    plugin_folder: str | Path = PLUGIN_FOLDER,
    protocol: str | None = None,
    incompatible_in_feishu: Iterable[str] = INCOMPATIBLE_IN_FEISHU,
    create_missing: bool = True,
) -> LoadResult:
    """Scan a plugin folder and load all enabled plugins.

    The first two parameters keep compatibility with the original project:
    ``load_plugins(config, logger)`` still works when ``config`` has a
    ``protocol`` attribute. For new projects, passing ``protocol=...`` and
    ``plugin_folder=...`` is usually clearer.
    """

    result = LoadResult()
    logger = _get_logger(logger)
    protocol_now = _resolve_protocol(config, protocol)
    incompatible_names = set(incompatible_in_feishu)
    folder = Path(plugin_folder)

    if not folder.exists():
        if create_missing:
            folder.mkdir(parents=True, exist_ok=True)
        else:
            result.failed.append(f"{folder} (插件目录不存在)")
            return result

    if not folder.is_dir():
        result.failed.append(f"{folder} (插件路径不是目录)")
        return result

    _ensure_import_parent(folder)

    for entry in folder.iterdir():
        filename = entry.name
        logger.debug("check file or directory: %s", filename)

        if filename == "__pycache__":
            logger.debug("Directory __pycache__ not load.")
            continue

        if filename.startswith(DISABLED_PREFIX):
            result.disabled.append(_display_name(filename[len(DISABLED_PREFIX) :]))
            continue

        plugin_base_name = _display_name(filename)
        if protocol_now == "feishu" and plugin_base_name in incompatible_names:
            result.disabled.append(plugin_base_name)
            logger.info("Feishu 模式跳过不兼容插件: %s", plugin_base_name)
            continue

        if entry.is_dir():
            setup_file = entry / "setup.py"
            if setup_file.exists():
                _load_single(setup_file, filename, result, logger)
            else:
                logger.warning("目录 %s 中缺少 setup.py 文件", filename)
                result.failed.append(f"{filename} (入口错误: 缺少 setup.py 文件)")
        elif entry.suffix in PLUGIN_EXTENSIONS:
            _load_single(entry, entry.stem, result, logger)
        else:
            logger.debug("跳过非插件文件或目录: %s", filename)

    logger.info("成功加载 %s 个插件", len(result.loaded))
    return result


def _register_module(
    module: ModuleType,
    unique_module_name: str,
    module_name: str,
    result: LoadResult,
    logger: logging.Logger,
) -> bool:
    """Validate and register an already executed plugin module."""

    if not (hasattr(module, "TRIGGHT_KEYWORD") and hasattr(module, "on_message")):
        result.failed.append(
            f"{module_name} (缺少 TRIGGHT_KEYWORD：触发标识符 或 on_message：触发函数后端)"
        )
        return False
    if not isinstance(module.TRIGGHT_KEYWORD, str):
        result.failed.append(f"{module_name} (TRIGGHT_KEYWORD 必须是字符串)")
        return False

    result.plugins.append(module)
    result.loaded.append(unique_module_name)
    result.loaded_display.append(module_name)

    help_message = getattr(module, "HELP_MESSAGE", None)
    if isinstance(help_message, str):
        for line in (line.strip() for line in help_message.splitlines()):
            if line:
                result._help_lines.append(line)

    logger.info(
        "已加载插件: %s (关键词: %s)",
        unique_module_name,
        module.TRIGGHT_KEYWORD,
    )
    return True


def _load_single(
    entry_path: Path,
    module_name: str,
    result: LoadResult,
    logger: logging.Logger,
) -> None:
    """Load one plugin entry file and clean ``sys.modules`` on failure."""

    unique_module_name = f"{module_name}_{uuid.uuid4().hex}"
    try:
        spec = importlib.util.spec_from_file_location(unique_module_name, entry_path)
        if spec is None or spec.loader is None:
            result.failed.append(f"{module_name} (入口错误: 无法创建导入规范)")
            return

        module = importlib.util.module_from_spec(spec)
        sys.modules[unique_module_name] = module
        spec.loader.exec_module(module)
        if not _register_module(module, unique_module_name, module_name, result, logger):
            sys.modules.pop(unique_module_name, None)
    except ImportError as exc:
        result.failed.append(f"{module_name} (导入错误: {exc})")
        logger.error(
            "加载插件 %s 失败，原因是: \n%s\n",
            unique_module_name,
            traceback.format_exc(),
        )
        sys.modules.pop(unique_module_name, None)
    except Exception as exc:
        result.failed.append(f"{module_name} (其他错误: {exc})")
        logger.error(
            "加载插件 %s 失败: \n%s\n",
            unique_module_name,
            traceback.format_exc(),
        )
        sys.modules.pop(unique_module_name, None)


def _resolve_protocol(config: Any, protocol: str | None) -> str:
    if protocol is not None:
        return str(protocol).lower()
    if config is None:
        return ""
    if isinstance(config, dict):
        return str(config.get("protocol") or config.get("Protocol") or "").lower()
    return str(getattr(config, "protocol", "")).lower()


def _get_logger(logger: logging.Logger | None) -> logging.Logger:
    if logger is not None:
        return logger
    fallback = logging.getLogger("jianer.plugins.loader")
    if not fallback.handlers:
        fallback.addHandler(logging.NullHandler())
    return fallback


def _display_name(filename: str) -> str:
    return Path(filename).stem


def _ensure_import_parent(folder: Path) -> None:
    parent = str(folder.resolve().parent)
    if parent not in sys.path:
        sys.path.insert(0, parent)

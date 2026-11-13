"""Built-in unified message and command plugin for JianerCore."""

from __future__ import annotations

import inspect
import threading
from typing import Any, Callable

from arclet.alconna import Alconna, Args, Arparma, MultiVar

from ....unimessage import (
    Receipt,
    Target,
    UniMessage,
    bind_message_context,
    reset_message_context,
)
from ...metadata import PluginMetadata
from ...runtime import current_plugin_owner

try:
    from arclet.alconna import config as alconna_config

    # Staged reloads keep the old and new generations alive at the same time.
    # Production plugin sets can exceed Alconna's default 200-command ceiling
    # during that overlap.
    if alconna_config.command_max_count < 512:
        alconna_config.command_max_count = 512
except Exception:
    pass

__plugin_meta__ = PluginMetadata(
    name="jianerbot-plugin-alconna",
    description="Unified message sending and Alconna command matching for JianerCore plugins.",
)

_MATCHERS: dict[str, dict[str, list["CommandMatcher"]]] = {}
_LEGACY_MATCHERS: list["CommandMatcher"] = []
_MATCHER_LOCK = threading.RLock()


class CommandMatcher:
    """Alconna-backed command matcher."""

    def __init__(self, command: str | Alconna):
        self.command = _coerce_alconna(command)
        self.handlers: list[Callable[..., Any]] = []

    def handle(self) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            self.handlers.append(func)
            _register_matcher(self)
            return func

        return decorator

    async def dispatch(self, event: Any, actions: Any) -> bool:
        message = _event_message_text(event)
        result = self.command.parse(message)
        if not result.matched:
            return False
        context_tokens = bind_message_context(event, actions)
        try:
            handled = False
            for handler in self.handlers:
                kwargs = _build_handler_kwargs(handler, event, actions, result)
                response = handler(**kwargs)
                if inspect.isawaitable(response):
                    response = await response
                if response is not False:
                    handled = True
            return handled
        finally:
            reset_message_context(context_tokens)


def Command(command: str | Alconna) -> CommandMatcher:
    return CommandMatcher(command)


def on_alconna(command: str | Alconna) -> CommandMatcher:
    return Command(command)


async def on_message(event: Any, actions: Any) -> bool:
    handled = False
    for matcher in _matchers_for_current_manager():
        if await matcher.dispatch(event, actions):
            handled = True
            break
    return handled


def setup(client: Any, manager: Any) -> None:
    """Setup hook kept for plugin-manager compatibility."""


def _coerce_alconna(command: str | Alconna) -> Alconna:
    if isinstance(command, Alconna):
        return command
    parts = command.split()
    if not parts:
        raise ValueError("command pattern cannot be empty")
    command_name = parts[0]
    args = []
    for token in parts[1:]:
        if token.startswith("<") and token.endswith(">"):
            args.append(token[1:-1])
    if len(args) == 1 and len(parts) == 2:
        return Alconna(command_name, Args[args[0], MultiVar(str)])
    if len(args) == 1:
        return Alconna(command_name, Args[args[0], str])
    return Alconna(command_name)


def _event_message_text(event: Any) -> str:
    if hasattr(event, "msg_str"):
        return str(event.msg_str)
    if hasattr(event, "message"):
        return str(event.message)
    return str(event)


def _build_handler_kwargs(
    handler: Callable[..., Any],
    event: Any,
    actions: Any,
    result: Arparma,
) -> dict[str, Any]:
    signature = inspect.signature(handler)
    values = _matched_values(result)
    kwargs: dict[str, Any] = {}
    for name, parameter in signature.parameters.items():
        if name == "event":
            kwargs[name] = event
        elif name == "actions":
            kwargs[name] = actions
        elif name in {"result", "arparma"}:
            kwargs[name] = result
        elif name == "user_message":
            kwargs[name] = getattr(event, "message", None)
        elif name in values:
            kwargs[name] = _normalize_value(values[name], parameter.annotation)
        elif parameter.default is inspect.Parameter.empty:
            raise TypeError(f"missing command handler argument: {name}")
    return kwargs


def _matched_values(result: Arparma) -> dict[str, Any]:
    values: dict[str, Any] = {}
    values.update(getattr(result, "main_args", {}))
    values.update(getattr(result, "other_args", {}))
    for option_name, option_result in getattr(result, "options", {}).items():
        values.setdefault(option_name, option_result)
        option_args = getattr(option_result, "args", None)
        if isinstance(option_args, dict):
            values.update(option_args)
    return values


def _normalize_value(value: Any, annotation: Any) -> Any:
    if isinstance(value, tuple) and annotation in {str, inspect.Signature.empty}:
        return " ".join(str(item) for item in value)
    return value


def _clear_matchers() -> None:
    """Test helper for clearing global command registrations."""

    with _MATCHER_LOCK:
        _MATCHERS.clear()
        _LEGACY_MATCHERS.clear()


def _register_matcher(matcher: CommandMatcher) -> None:
    owner = current_plugin_owner()
    with _MATCHER_LOCK:
        if owner is None:
            if matcher not in _LEGACY_MATCHERS:
                _LEGACY_MATCHERS.append(matcher)
            return
        plugin_matchers = _MATCHERS.setdefault(owner.manager_id, {}).setdefault(
            owner.plugin_id, []
        )
        if matcher not in plugin_matchers:
            plugin_matchers.append(matcher)


def _matchers_for_current_manager() -> list[CommandMatcher]:
    owner = current_plugin_owner()
    with _MATCHER_LOCK:
        result: list[CommandMatcher] = []
        if owner is not None:
            for plugin_matchers in _MATCHERS.get(owner.manager_id, {}).values():
                result.extend(plugin_matchers)
        # Commands created directly by host code predate manager ownership.
        result.extend(_LEGACY_MATCHERS)
        return result


def _remove_manager_matchers(manager_id: str) -> None:
    """Remove only command registrations owned by one PluginManager."""

    with _MATCHER_LOCK:
        _MATCHERS.pop(manager_id, None)


def _remove_plugin_matchers(manager_id: str, plugin_id: str) -> None:
    """Remove registrations created by one failed or unloaded plugin."""

    with _MATCHER_LOCK:
        manager_matchers = _MATCHERS.get(manager_id)
        if manager_matchers is None:
            return
        manager_matchers.pop(plugin_id, None)
        if not manager_matchers:
            _MATCHERS.pop(manager_id, None)


__all__ = [
    "Command",
    "CommandMatcher",
    "Receipt",
    "Target",
    "UniMessage",
    "on_message",
    "on_alconna",
]

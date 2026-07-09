"""Built-in unified message and command plugin for JianerCore."""

from __future__ import annotations

import contextvars
import inspect
from dataclasses import dataclass
from typing import Any, Callable

from arclet.alconna import Alconna, Args, Arparma, MultiVar

from .... import common, segments
from ...metadata import PluginMetadata

__plugin_meta__ = PluginMetadata(
    name="jianerbot-plugin-alconna",
    description="Unified message sending and Alconna command matching for JianerCore plugins.",
)

_CURRENT_EVENT: contextvars.ContextVar[Any] = contextvars.ContextVar("jianer_alconna_event")
_CURRENT_ACTIONS: contextvars.ContextVar[Any] = contextvars.ContextVar("jianer_alconna_actions")
_MATCHERS: list["CommandMatcher"] = []


@dataclass(frozen=True)
class Target:
    """Unified send target."""

    id: int | str
    is_private: bool = False

    @classmethod
    def group(cls, group_id: int | str) -> "Target":
        return cls(group_id, is_private=False)

    @classmethod
    def private(cls, user_id: int | str) -> "Target":
        return cls(user_id, is_private=True)

    @classmethod
    def from_event(cls, event: Any) -> "Target":
        group_id = getattr(event, "group_id", None)
        if group_id is not None:
            return cls.group(group_id)
        user_id = getattr(event, "user_id", None)
        if user_id is None:
            raise ValueError("event does not contain group_id or user_id")
        return cls.private(user_id)

    async def send(self, message: str | common.Message | "UniMessage", actions: Any | None = None) -> "Receipt":
        if actions is None:
            actions = _CURRENT_ACTIONS.get(None)
        if actions is None:
            raise RuntimeError("no actions available for sending message")
        if isinstance(message, UniMessage):
            message = message.to_message()
        elif isinstance(message, str):
            message = UniMessage.text(message).to_message()
        if self.is_private:
            raw = await actions.send(message, user_id=self.id)
        else:
            raw = await actions.send(message, group_id=self.id)
        return Receipt(raw=raw, target=self, actions=actions)


@dataclass
class Receipt:
    """Unified send receipt."""

    raw: Any
    target: Target
    actions: Any

    @property
    def message_id(self) -> Any:
        data = getattr(self.raw, "data", None)
        if isinstance(data, dict) and "message_id" in data:
            return data["message_id"]
        if data is not None and hasattr(data, "message_id"):
            return data.message_id
        raw = getattr(self.raw, "raw", None)
        if isinstance(raw, dict):
            data = raw.get("data")
            if isinstance(data, dict):
                return data.get("message_id")
        return None

    async def reply(self, message: str | common.Message | "UniMessage") -> "Receipt":
        return await self.target.send(message, self.actions)

    async def recall(self) -> None:
        message_id = self.message_id
        if message_id is None:
            raise RuntimeError("receipt does not contain message_id")
        deleter = getattr(self.actions, "del_message", None)
        if deleter is None:
            raise RuntimeError("actions does not support del_message")
        result = deleter(message_id)
        if hasattr(result, "__await__"):
            await result


class UniMessage:
    """Unified message chain backed by JianerCore message segments."""

    def __init__(self, *items: Any):
        self.contents: list[Any] = []
        for item in items:
            self.append(item)

    @classmethod
    def text(cls, text: str) -> "UniMessage":
        return cls(segments.Text(text))

    @classmethod
    def image(cls, file: str, summary: str = "[Image]") -> "UniMessage":
        return cls(segments.Image(file=file, summary=summary))

    @classmethod
    def at(cls, user_id: int | str) -> "UniMessage":
        return cls(segments.At(str(user_id)))

    @classmethod
    def reply(cls, message_id: int | str) -> "UniMessage":
        return cls(segments.Reply(str(message_id)))

    def __add__(self, other: Any) -> "UniMessage":
        result = UniMessage()
        result.contents = list(self.contents)
        result.append(other)
        return result

    def __radd__(self, other: Any) -> "UniMessage":
        result = UniMessage()
        result.append(other)
        result.contents.extend(self.contents)
        return result

    def append(self, item: Any) -> "UniMessage":
        if isinstance(item, UniMessage):
            self.contents.extend(item.contents)
        elif isinstance(item, common.Message):
            self.contents.extend(item.contents)
        elif isinstance(item, str):
            self.contents.append(segments.Text(item))
        else:
            self.contents.append(item)
        return self

    def to_message(self) -> common.Message:
        return common.Message(*self.contents)

    async def send(
        self: "UniMessage | str",
        *args: "UniMessage | str",
        target: Target | None = None,
        actions: Any | None = None,
        event: Any | None = None,
    ) -> Receipt:
        if args:
            message = UniMessage()
            message.append(UniMessage.text(self) if isinstance(self, str) else self)
            for item in args:
                message.append(item)
        else:
            message = UniMessage.text(self) if isinstance(self, str) else self
        if actions is None:
            actions = _CURRENT_ACTIONS.get(None)
        if event is None:
            event = _CURRENT_EVENT.get(None)
        if target is None:
            if event is None:
                raise RuntimeError("no target or current event available for sending message")
            target = Target.from_event(event)
        return await target.send(message, actions)

    def __str__(self) -> str:
        return str(self.to_message())


class CommandMatcher:
    """Alconna-backed command matcher."""

    def __init__(self, command: str | Alconna):
        self.command = _coerce_alconna(command)
        self.handlers: list[Callable[..., Any]] = []

    def handle(self) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            self.handlers.append(func)
            if self not in _MATCHERS:
                _MATCHERS.append(self)
            return func

        return decorator

    async def dispatch(self, event: Any, actions: Any) -> bool:
        message = _event_message_text(event)
        result = self.command.parse(message)
        if not result.matched:
            return False
        event_token = _CURRENT_EVENT.set(event)
        actions_token = _CURRENT_ACTIONS.set(actions)
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
            _CURRENT_EVENT.reset(event_token)
            _CURRENT_ACTIONS.reset(actions_token)


def Command(command: str | Alconna) -> CommandMatcher:
    return CommandMatcher(command)


def on_alconna(command: str | Alconna) -> CommandMatcher:
    return Command(command)


async def on_message(event: Any, actions: Any) -> bool:
    handled = False
    for matcher in list(_MATCHERS):
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

    _MATCHERS.clear()


__all__ = [
    "Command",
    "CommandMatcher",
    "Receipt",
    "Target",
    "UniMessage",
    "on_message",
    "on_alconna",
]

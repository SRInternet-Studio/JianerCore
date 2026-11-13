"""Protocol-neutral message targets and message chains for JianerCore."""

from __future__ import annotations

import contextvars
from dataclasses import dataclass
from typing import Any

from . import common, segments

_CURRENT_EVENT: contextvars.ContextVar[Any] = contextvars.ContextVar(
    "jianer_unimessage_event"
)
_CURRENT_ACTIONS: contextvars.ContextVar[Any] = contextvars.ContextVar(
    "jianer_unimessage_actions"
)


def bind_message_context(event: Any, actions: Any) -> tuple[Any, Any]:
    """Bind the event and actions used by ``UniMessage.send`` shortcuts."""

    return (
        _CURRENT_EVENT.set(event),
        _CURRENT_ACTIONS.set(actions),
    )


def reset_message_context(tokens: tuple[Any, Any]) -> None:
    event_token, actions_token = tokens
    _CURRENT_ACTIONS.reset(actions_token)
    _CURRENT_EVENT.reset(event_token)


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

    async def send(
        self,
        message: str | common.Message | "UniMessage",
        actions: Any | None = None,
    ) -> "Receipt":
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

    async def reply(
        self,
        message: str | common.Message | "UniMessage",
    ) -> "Receipt":
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
                raise RuntimeError(
                    "no target or current event available for sending message"
                )
            target = Target.from_event(event)
        return await target.send(message, actions)

    def __str__(self) -> str:
        return str(self.to_message())


__all__ = [
    "Receipt",
    "Target",
    "UniMessage",
    "bind_message_context",
    "reset_message_context",
]

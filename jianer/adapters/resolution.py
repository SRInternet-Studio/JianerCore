from __future__ import annotations

from typing import Iterable, Optional

from .contracts import (
    ConversationKey,
    ConversationKind,
    ExternalId,
    ReferenceResolution,
    ResolutionErrorCode,
    ResolutionStatus,
    normalize_external_id,
)


def numeric_external_id(value, field_name: str = "external_id") -> int:
    normalized = normalize_external_id(value, field_name)
    if not normalized.isdecimal():
        raise ValueError(f"{field_name} must be a decimal identifier for this adapter")
    return int(normalized)


def positive_timeout_seconds(value) -> float:
    try:
        normalized = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("timeout_seconds must be positive") from exc
    if normalized <= 0:
        raise ValueError("timeout_seconds must be positive")
    return normalized


def reference_failure(
    status: ResolutionStatus,
    error_code: ResolutionErrorCode,
    *,
    message_id=None,
    conversation: Optional[ConversationKey] = None,
) -> ReferenceResolution:
    normalized_id = None
    if message_id is not None:
        try:
            normalized_id = normalize_external_id(message_id, "message_id")
        except ValueError:
            normalized_id = None
    return ReferenceResolution(
        status=status,
        error_code=error_code,
        message_id=normalized_id,
        conversation=conversation,
        sender_id=None,
        sent_at=None,
        segments=(),
    )


def reference_success(
    *,
    expected: ConversationKey,
    message_id,
    resolved_kind: ConversationKind,
    resolved_conversation_id,
    sender_id,
    sent_at,
    segments: Iterable,
) -> ReferenceResolution:
    try:
        resolved_id = normalize_external_id(resolved_conversation_id, "conversation_id")
        normalized_message_id = normalize_external_id(message_id, "message_id")
        normalized_sender_id = normalize_external_id(sender_id, "sender_id")
    except ValueError:
        return reference_failure(
            ResolutionStatus.ERROR,
            ResolutionErrorCode.MALFORMED_RESPONSE,
            message_id=message_id,
            conversation=expected,
        )

    if resolved_kind is not expected.kind or resolved_id != expected.conversation_id:
        return reference_failure(
            ResolutionStatus.REJECTED,
            ResolutionErrorCode.CONVERSATION_MISMATCH,
            message_id=message_id,
            conversation=expected,
        )

    try:
        timestamp = int(sent_at) if sent_at is not None else None
    except (TypeError, ValueError):
        timestamp = None
    return ReferenceResolution(
        status=ResolutionStatus.OK,
        error_code=None,
        message_id=normalized_message_id,
        conversation=expected,
        sender_id=normalized_sender_id,
        sent_at=timestamp,
        segments=tuple(segments),
    )


def unsupported_reference(
    message_id=None,
    conversation: Optional[ConversationKey] = None,
) -> ReferenceResolution:
    return reference_failure(
        ResolutionStatus.UNSUPPORTED,
        ResolutionErrorCode.CAPABILITY_UNAVAILABLE,
        message_id=message_id,
        conversation=conversation,
    )


def response_data_raw(response) -> dict:
    data = getattr(response, "data", None)
    raw = getattr(data, "raw", None)
    if isinstance(raw, dict):
        return raw
    response_raw = getattr(response, "raw", None)
    if isinstance(response_raw, dict) and isinstance(response_raw.get("data"), dict):
        return response_raw["data"]
    return {}


def response_segments(response) -> tuple:
    data = getattr(response, "data", None)
    message = getattr(data, "message", None)
    if message is None:
        return ()
    try:
        return tuple(message)
    except TypeError:
        return ()


def onebot_reference_identity(raw: dict, expected: ConversationKey):
    message_type = str(raw.get("message_type") or "").casefold()
    sender = raw.get("sender") if isinstance(raw.get("sender"), dict) else {}
    sender_id = sender.get("user_id") or raw.get("user_id")
    if message_type == "group":
        return ConversationKind.GROUP, raw.get("group_id"), sender_id
    if message_type == "private":
        peer_id = raw.get("target_id") or raw.get("peer_id")
        if peer_id is None and str(sender_id or "") != expected.self_id:
            peer_id = sender_id
        return ConversationKind.PRIVATE, peer_id, sender_id
    return None, None, sender_id

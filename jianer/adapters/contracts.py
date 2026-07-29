from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import FrozenSet, Optional, Protocol, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from .res import SegmentBase


ExternalId = str
ProtocolName = str


def normalize_external_id(value, field_name: str = "external_id") -> ExternalId:
    if value is None or isinstance(value, bool):
        raise ValueError(f"{field_name} must be a non-empty string identifier")
    normalized = str(value).strip()
    if not normalized:
        raise ValueError(f"{field_name} must be a non-empty string identifier")
    return normalized


def normalize_optional_external_id(value, field_name: str = "external_id") -> Optional[ExternalId]:
    if value is None:
        return None
    return normalize_external_id(value, field_name)


def normalize_protocol_name(value) -> ProtocolName:
    normalized = normalize_external_id(value, "protocol").casefold()
    return normalized


class ConversationKind(str, Enum):
    PRIVATE = "private"
    GROUP = "group"


@dataclass(frozen=True)
class ConversationKey:
    protocol: ProtocolName
    self_id: ExternalId
    kind: ConversationKind
    conversation_id: ExternalId
    preset: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "protocol", normalize_protocol_name(self.protocol))
        object.__setattr__(self, "self_id", normalize_external_id(self.self_id, "self_id"))
        object.__setattr__(
            self,
            "conversation_id",
            normalize_external_id(self.conversation_id, "conversation_id"),
        )
        preset = str(self.preset or "").strip()
        if not preset:
            raise ValueError("preset must be a non-empty stable identifier")
        object.__setattr__(self, "preset", preset)
        if not isinstance(self.kind, ConversationKind):
            object.__setattr__(self, "kind", ConversationKind(str(self.kind)))

    @classmethod
    def from_event(cls, event, preset: str) -> "ConversationKey":
        group_id = getattr(event, "group_id", None)
        kind = ConversationKind.GROUP if group_id is not None else ConversationKind.PRIVATE
        conversation_id = getattr(event, "conversation_id", None)
        if conversation_id is None:
            conversation_id = group_id if kind is ConversationKind.GROUP else getattr(event, "user_id", None)
        return cls(
            protocol=getattr(event, "protocol", ""),
            self_id=getattr(event, "self_id", None),
            kind=kind,
            conversation_id=conversation_id,
            preset=preset,
        )


class Capability(str, Enum):
    """Adapter features.

    SEND_* values describe trusted outbound transport only. They do not grant
    permission to ingest a user-controlled media locator; callers must require
    RESOLVE_MEDIA and a successful MediaResolution before using untrusted media.
    """

    RESOLVE_REFERENCE = "resolve_reference"
    RESOLVE_MEDIA = "resolve_media"
    SEND_REPLY = "send_reply"
    SEND_IMAGE = "send_image"
    SEND_AUDIO = "send_audio"
    NATIVE_GROUP_FORWARD = "native_group_forward"
    RESOLVE_FORWARD = "resolve_forward"


Capabilities = FrozenSet[Capability]


class ResolutionStatus(str, Enum):
    OK = "ok"
    UNSUPPORTED = "unsupported"
    NOT_FOUND = "not_found"
    REJECTED = "rejected"
    ERROR = "error"


class ResolutionErrorCode(str, Enum):
    CAPABILITY_UNAVAILABLE = "capability_unavailable"
    INVALID_ID = "invalid_id"
    INVALID_SOURCE = "invalid_source"
    REFERENCE_NOT_FOUND = "reference_not_found"
    MEDIA_NOT_FOUND = "media_not_found"
    CONVERSATION_MISMATCH = "conversation_mismatch"
    SCHEME_NOT_ALLOWED = "scheme_not_allowed"
    ORIGIN_NOT_ALLOWED = "origin_not_allowed"
    LOCAL_PATH_DENIED = "local_path_denied"
    REDIRECT_LIMIT = "redirect_limit"
    TOO_LARGE = "too_large"
    MIME_UNSUPPORTED = "mime_unsupported"
    MIME_MISMATCH = "mime_mismatch"
    DECODE_FAILED = "decode_failed"
    INVALID_TIMEOUT = "invalid_timeout"
    TIMEOUT = "timeout"
    UPSTREAM_DENIED = "upstream_denied"
    UPSTREAM_ERROR = "upstream_error"
    MALFORMED_RESPONSE = "malformed_response"


@dataclass(frozen=True)
class ReferenceResolution:
    status: ResolutionStatus
    error_code: Optional[ResolutionErrorCode]
    message_id: Optional[ExternalId]
    conversation: Optional[ConversationKey]
    sender_id: Optional[ExternalId]
    sent_at: Optional[int]
    segments: Tuple["SegmentBase", ...] = ()

    def __post_init__(self) -> None:
        if self.status is ResolutionStatus.OK:
            if self.error_code is not None:
                raise ValueError("successful reference resolutions cannot have an error code")
            if (
                self.message_id is None
                or self.conversation is None
                or self.sender_id is None
            ):
                raise ValueError("successful reference resolutions require identity fields")
        elif self.error_code is None:
            raise ValueError("failed reference resolutions require an error code")


class MediaSourceKind(str, Enum):
    REMOTE_URL = "remote_url"
    DATA_URI = "data_uri"
    LOCAL_FILE = "local_file"
    ADAPTER_RESOURCE = "adapter_resource"


class MediaKind(str, Enum):
    IMAGE = "image"
    AUDIO = "audio"
    VIDEO = "video"
    FILE = "file"


@dataclass(frozen=True)
class MediaRequest:
    kind: MediaSourceKind
    media_kind: MediaKind
    locator: str = field(repr=False)
    message_id: Optional[ExternalId] = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, MediaSourceKind):
            object.__setattr__(self, "kind", MediaSourceKind(str(self.kind)))
        if not isinstance(self.media_kind, MediaKind):
            object.__setattr__(self, "media_kind", MediaKind(str(self.media_kind)))
        locator = str(self.locator or "").strip()
        if not locator:
            raise ValueError("media locator must be non-empty")
        object.__setattr__(self, "locator", locator)
        if self.message_id is not None:
            object.__setattr__(
                self,
                "message_id",
                normalize_external_id(self.message_id, "message_id"),
            )


DEFAULT_IMAGE_MIME_TYPES = frozenset(
    {"image/jpeg", "image/png", "image/gif", "image/webp"}
)


@dataclass(frozen=True)
class MediaPolicy:
    max_bytes: int = 10 * 1024 * 1024
    connect_timeout_seconds: float = 3.0
    total_timeout_seconds: float = 15.0
    max_redirects: int = 3
    allowed_remote_origins: FrozenSet[str] = frozenset()
    allowed_local_roots: Tuple[Path, ...] = ()
    allowed_mime_types: FrozenSet[str] = DEFAULT_IMAGE_MIME_TYPES

    def __post_init__(self) -> None:
        if self.max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        if self.connect_timeout_seconds <= 0 or self.total_timeout_seconds <= 0:
            raise ValueError("media timeouts must be positive")
        if self.connect_timeout_seconds > self.total_timeout_seconds:
            raise ValueError("connect timeout cannot exceed total timeout")
        if self.max_redirects < 0:
            raise ValueError("max_redirects cannot be negative")
        object.__setattr__(
            self,
            "allowed_remote_origins",
            frozenset(str(value).rstrip("/").casefold() for value in self.allowed_remote_origins),
        )
        object.__setattr__(
            self,
            "allowed_local_roots",
            tuple(Path(value) for value in self.allowed_local_roots),
        )
        object.__setattr__(
            self,
            "allowed_mime_types",
            frozenset(str(value).casefold() for value in self.allowed_mime_types),
        )


DEFAULT_MEDIA_POLICY = MediaPolicy()


@dataclass(frozen=True)
class MediaResolution:
    status: ResolutionStatus
    error_code: Optional[ResolutionErrorCode]
    mime: Optional[str]
    size: int
    data: Optional[bytes]
    source: str

    def __post_init__(self) -> None:
        if self.status is ResolutionStatus.OK:
            if self.error_code is not None:
                raise ValueError("successful media resolutions cannot have an error code")
            if self.data is None or self.mime is None or self.size != len(self.data):
                raise ValueError("successful media resolutions require consistent data, MIME, and size")
        else:
            if self.error_code is None:
                raise ValueError("failed media resolutions require an error code")
            if self.data is not None or self.mime is not None or self.size != 0:
                raise ValueError("failed media resolutions cannot expose partial media data")


class AdapterActions(Protocol):
    protocol: ProtocolName
    capabilities: Capabilities

    async def resolve_reference(
        self,
        message_id: ExternalId,
        *,
        conversation: ConversationKey,
        timeout_seconds: float = 10.0,
    ) -> ReferenceResolution:
        ...

    async def resolve_media(
        self,
        request: MediaRequest,
        *,
        conversation: ConversationKey,
        policy: MediaPolicy = DEFAULT_MEDIA_POLICY,
    ) -> MediaResolution:
        ...

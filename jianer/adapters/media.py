from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import ipaddress
import os
import re
import socket
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import unquote, urljoin, urlsplit

import aiohttp

from .contracts import (
    MediaKind,
    MediaPolicy,
    MediaRequest,
    MediaResolution,
    MediaSourceKind,
    ResolutionErrorCode,
    ResolutionStatus,
)


_DATA_URI_RE = re.compile(
    r"^data:(?P<mime>[-\w.+]+/[-\w.+]+);base64,(?P<payload>.*)$",
    re.IGNORECASE | re.DOTALL,
)


def media_failure(
    request: MediaRequest,
    status: ResolutionStatus,
    error_code: ResolutionErrorCode,
) -> MediaResolution:
    return MediaResolution(
        status=status,
        error_code=error_code,
        mime=None,
        size=0,
        data=None,
        source=safe_source_label(request),
    )


def unsupported_media(request: MediaRequest) -> MediaResolution:
    return media_failure(
        request,
        ResolutionStatus.UNSUPPORTED,
        ResolutionErrorCode.CAPABILITY_UNAVAILABLE,
    )


def safe_source_label(request: MediaRequest) -> str:
    locator = request.locator
    if request.kind is MediaSourceKind.REMOTE_URL:
        try:
            parsed = urlsplit(locator)
            host = parsed.hostname or "invalid"
            port = f":{parsed.port}" if parsed.port else ""
            return f"{parsed.scheme.casefold()}://{host.casefold()}{port}"
        except (TypeError, ValueError):
            return "remote:invalid"
    if request.kind is MediaSourceKind.LOCAL_FILE:
        try:
            if locator.casefold().startswith("file:"):
                local_path = unquote(urlsplit(locator).path)
            else:
                local_path = locator
            return f"local:{Path(local_path).name or 'file'}"
        except (TypeError, ValueError):
            return "local:file"
    if request.kind is MediaSourceKind.DATA_URI:
        match = _DATA_URI_RE.match(locator)
        return f"data:{match.group('mime').casefold()}" if match else "data:invalid"
    fingerprint = hashlib.sha256(locator.encode("utf-8", errors="replace")).hexdigest()[:12]
    return f"adapter:{fingerprint}"


def _sniff_mime(data: bytes) -> Optional[str]:
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data.startswith(b"ID3") or (
        len(data) >= 2
        and data[0] == 0xFF
        and data[1] & 0xE0 == 0xE0
    ):
        return "audio/mpeg"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return "audio/wav"
    if data.startswith(b"OggS") and (
        b"OpusHead" in data[:4096]
        or b"\x01vorbis" in data[:4096]
        or b"\x03vorbis" in data[:4096]
    ):
        return "audio/ogg"
    if data.startswith(b"fLaC"):
        return "audio/flac"
    if len(data) >= 12 and data[4:8] == b"ftyp":
        return "video/mp4"
    if data.startswith(b"\x1a\x45\xdf\xa3") and b"webm" in data[:4096].lower():
        return "video/webm"
    return None


def _finish_media(
    request: MediaRequest,
    policy: MediaPolicy,
    data: bytes,
    declared_mime: Optional[str] = None,
) -> MediaResolution:
    if len(data) > policy.max_bytes:
        return media_failure(
            request,
            ResolutionStatus.REJECTED,
            ResolutionErrorCode.TOO_LARGE,
        )
    detected_mime = _sniff_mime(data)
    if detected_mime is None or detected_mime not in policy.allowed_mime_types:
        return media_failure(
            request,
            ResolutionStatus.REJECTED,
            ResolutionErrorCode.MIME_UNSUPPORTED,
        )
    expected_family = {
        MediaKind.IMAGE: "image/",
        MediaKind.AUDIO: "audio/",
        MediaKind.VIDEO: "video/",
    }.get(request.media_kind)
    if expected_family is not None and not detected_mime.startswith(expected_family):
        return media_failure(
            request,
            ResolutionStatus.REJECTED,
            ResolutionErrorCode.MIME_MISMATCH,
        )
    if declared_mime:
        normalized_declared = declared_mime.split(";", 1)[0].strip().casefold()
        if normalized_declared not in ("application/octet-stream", detected_mime):
            return media_failure(
                request,
                ResolutionStatus.REJECTED,
                ResolutionErrorCode.MIME_MISMATCH,
            )
    return MediaResolution(
        status=ResolutionStatus.OK,
        error_code=None,
        mime=detected_mime,
        size=len(data),
        data=data,
        source=safe_source_label(request),
    )


def _origin_from_url(url: str) -> str:
    parsed = urlsplit(url)
    scheme = parsed.scheme.casefold()
    if scheme not in ("http", "https"):
        raise ValueError("unsupported scheme")
    if parsed.username is not None or parsed.password is not None:
        raise PermissionError("URL credentials are not allowed")
    host = (parsed.hostname or "").casefold()
    if not host:
        raise ValueError("missing URL host")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("invalid URL port") from exc
    default_port = 443 if scheme == "https" else 80
    return f"{scheme}://{host}:{port or default_port}"


def _normalized_policy_origin(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme.casefold() not in ("http", "https")
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("invalid allowlisted origin")
    port = parsed.port or (443 if parsed.scheme.casefold() == "https" else 80)
    return f"{parsed.scheme.casefold()}://{parsed.hostname.casefold()}:{port}"


async def _validate_remote_target(
    url: str,
    policy: MediaPolicy,
) -> Tuple[str, str, int, List[tuple]]:
    origin = _origin_from_url(url)
    allowed_remote = {_normalized_policy_origin(value) for value in policy.allowed_remote_origins}
    if origin not in allowed_remote:
        raise PermissionError("remote origin is not allowlisted")

    parsed = urlsplit(url)
    loop = asyncio.get_running_loop()
    port = parsed.port or (443 if parsed.scheme.casefold() == "https" else 80)
    infos = await loop.getaddrinfo(
        parsed.hostname,
        port,
        type=socket.SOCK_STREAM,
    )
    addresses = {item[4][0] for item in infos}
    if not addresses:
        raise OSError("remote host did not resolve")
    for address in addresses:
        if not ipaddress.ip_address(address).is_global:
            raise PermissionError("remote host resolved to a non-public address")
    return origin, str(parsed.hostname), port, infos


class _PinnedResolver(aiohttp.abc.AbstractResolver):
    def __init__(self) -> None:
        self._records: Dict[Tuple[str, int], List[tuple]] = {}

    def pin(self, host: str, port: int, records: List[tuple]) -> None:
        self._records[(host.casefold(), int(port))] = list(records)

    async def resolve(self, host: str, port: int = 0, family: int = socket.AF_INET):
        records = self._records.get((host.casefold(), int(port)))
        if not records:
            raise OSError("unvalidated media host")
        resolved = []
        for record in records:
            record_family, socket_type, protocol, _, sockaddr = record
            if family not in (socket.AF_UNSPEC, 0) and record_family != family:
                continue
            resolved.append({
                "hostname": host,
                "host": sockaddr[0],
                "port": int(port),
                "family": record_family,
                "proto": protocol,
                "flags": socket.AI_NUMERICHOST,
            })
        if not resolved:
            raise OSError("validated media host has no compatible address")
        return resolved

    async def close(self) -> None:
        self._records.clear()


async def _resolve_remote(request: MediaRequest, policy: MediaPolicy) -> MediaResolution:
    async def download() -> MediaResolution:
        current_url = request.locator
        resolver = _PinnedResolver()
        connector = aiohttp.TCPConnector(
            resolver=resolver,
            use_dns_cache=False,
        )
        timeout = aiohttp.ClientTimeout(
            total=policy.total_timeout_seconds,
            connect=policy.connect_timeout_seconds,
            sock_connect=policy.connect_timeout_seconds,
            sock_read=policy.total_timeout_seconds,
        )
        async with aiohttp.ClientSession(
            connector=connector,
            timeout=timeout,
            auto_decompress=True,
            trust_env=False,
        ) as client:
            for redirect_count in range(policy.max_redirects + 1):
                try:
                    _, host, port, records = await _validate_remote_target(current_url, policy)
                    resolver.pin(host, port, records)
                except ValueError:
                    return media_failure(
                        request,
                        ResolutionStatus.REJECTED,
                        ResolutionErrorCode.SCHEME_NOT_ALLOWED,
                    )
                except PermissionError:
                    return media_failure(
                        request,
                        ResolutionStatus.REJECTED,
                        ResolutionErrorCode.ORIGIN_NOT_ALLOWED,
                    )
                except OSError:
                    return media_failure(
                        request,
                        ResolutionStatus.ERROR,
                        ResolutionErrorCode.UPSTREAM_ERROR,
                    )

                try:
                    async with client.get(current_url, allow_redirects=False) as response:
                        if response.status in (301, 302, 303, 307, 308):
                            location = response.headers.get("location")
                            if not location:
                                return media_failure(
                                    request,
                                    ResolutionStatus.ERROR,
                                    ResolutionErrorCode.MALFORMED_RESPONSE,
                                )
                            if redirect_count >= policy.max_redirects:
                                return media_failure(
                                    request,
                                    ResolutionStatus.REJECTED,
                                    ResolutionErrorCode.REDIRECT_LIMIT,
                                )
                            current_url = urljoin(current_url, location)
                            continue
                        if response.status in (401, 403):
                            return media_failure(
                                request,
                                ResolutionStatus.ERROR,
                                ResolutionErrorCode.UPSTREAM_DENIED,
                            )
                        if response.status == 404:
                            return media_failure(
                                request,
                                ResolutionStatus.NOT_FOUND,
                                ResolutionErrorCode.MEDIA_NOT_FOUND,
                            )
                        if response.status >= 400:
                            return media_failure(
                                request,
                                ResolutionStatus.ERROR,
                                ResolutionErrorCode.UPSTREAM_ERROR,
                            )
                        content_length = response.headers.get("content-length")
                        if content_length:
                            try:
                                if int(content_length) > policy.max_bytes:
                                    return media_failure(
                                        request,
                                        ResolutionStatus.REJECTED,
                                        ResolutionErrorCode.TOO_LARGE,
                                    )
                            except ValueError:
                                return media_failure(
                                    request,
                                    ResolutionStatus.ERROR,
                                    ResolutionErrorCode.MALFORMED_RESPONSE,
                                )
                        chunks = []
                        size = 0
                        async for chunk in response.content.iter_chunked(64 * 1024):
                            size += len(chunk)
                            if size > policy.max_bytes:
                                return media_failure(
                                    request,
                                    ResolutionStatus.REJECTED,
                                    ResolutionErrorCode.TOO_LARGE,
                                )
                            chunks.append(chunk)
                        return _finish_media(
                            request,
                            policy,
                            b"".join(chunks),
                            response.headers.get("content-type"),
                        )
                except asyncio.TimeoutError:
                    return media_failure(
                        request,
                        ResolutionStatus.ERROR,
                        ResolutionErrorCode.TIMEOUT,
                    )
                except aiohttp.ClientError:
                    return media_failure(
                        request,
                        ResolutionStatus.ERROR,
                        ResolutionErrorCode.UPSTREAM_ERROR,
                    )
        return media_failure(
            request,
            ResolutionStatus.REJECTED,
            ResolutionErrorCode.REDIRECT_LIMIT,
        )

    try:
        return await asyncio.wait_for(download(), timeout=policy.total_timeout_seconds)
    except asyncio.TimeoutError:
        return media_failure(
            request,
            ResolutionStatus.ERROR,
            ResolutionErrorCode.TIMEOUT,
        )


async def _resolve_data(request: MediaRequest, policy: MediaPolicy) -> MediaResolution:
    locator = request.locator
    match = _DATA_URI_RE.match(locator)
    declared_mime = None
    payload = None
    if match:
        declared_mime = match.group("mime")
        payload = match.group("payload")
    elif locator.casefold().startswith("base64://"):
        payload = locator[len("base64://"):]
    elif locator.casefold().startswith("base64:"):
        payload = locator[len("base64:"):]
    if payload is None:
        return media_failure(
            request,
            ResolutionStatus.REJECTED,
            ResolutionErrorCode.INVALID_SOURCE,
        )
    if len(payload) > ((policy.max_bytes + 2) // 3) * 4 + 8:
        return media_failure(
            request,
            ResolutionStatus.REJECTED,
            ResolutionErrorCode.TOO_LARGE,
        )
    try:
        data = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError):
        return media_failure(
            request,
            ResolutionStatus.REJECTED,
            ResolutionErrorCode.DECODE_FAILED,
        )
    return _finish_media(request, policy, data, declared_mime)


def _local_path(locator: str) -> Path:
    if locator.casefold().startswith("file://"):
        parsed = urlsplit(locator)
        if parsed.netloc:
            raise PermissionError("remote file URI hosts are not allowed")
        raw_path = unquote(parsed.path)
        if os.name == "nt" and len(raw_path) >= 3 and raw_path[0] == "/" and raw_path[2] == ":":
            raw_path = raw_path[1:]
        path = Path(raw_path)
    else:
        path = Path(locator)
    raw = str(path)
    if raw.startswith("\\\\") or raw.startswith("\\\\.\\") or raw.startswith("\\\\?\\"):
        raise PermissionError("UNC and device paths are not allowed")
    return path.resolve(strict=True)


async def _resolve_local(request: MediaRequest, policy: MediaPolicy) -> MediaResolution:
    if not policy.allowed_local_roots:
        return media_failure(
            request,
            ResolutionStatus.REJECTED,
            ResolutionErrorCode.LOCAL_PATH_DENIED,
        )
    try:
        target = _local_path(request.locator)
        roots = tuple(root.resolve(strict=True) for root in policy.allowed_local_roots)
    except (OSError, ValueError, PermissionError):
        return media_failure(
            request,
            ResolutionStatus.REJECTED,
            ResolutionErrorCode.LOCAL_PATH_DENIED,
        )
    if not target.is_file() or not any(target == root or root in target.parents for root in roots):
        return media_failure(
            request,
            ResolutionStatus.REJECTED,
            ResolutionErrorCode.LOCAL_PATH_DENIED,
        )
    try:
        if target.stat().st_size > policy.max_bytes:
            return media_failure(
                request,
                ResolutionStatus.REJECTED,
                ResolutionErrorCode.TOO_LARGE,
            )

        def read_limited() -> bytes:
            with target.open("rb") as stream:
                return stream.read(policy.max_bytes + 1)

        data = await asyncio.to_thread(read_limited)
    except OSError:
        return media_failure(
            request,
            ResolutionStatus.ERROR,
            ResolutionErrorCode.UPSTREAM_ERROR,
        )
    return _finish_media(request, policy, data)


async def resolve_media_request(
    request: MediaRequest,
    policy: MediaPolicy,
) -> MediaResolution:
    if request.kind is MediaSourceKind.REMOTE_URL:
        return await _resolve_remote(request, policy)
    if request.kind is MediaSourceKind.DATA_URI:
        return await _resolve_data(request, policy)
    if request.kind is MediaSourceKind.LOCAL_FILE:
        return await _resolve_local(request, policy)
    return unsupported_media(request)

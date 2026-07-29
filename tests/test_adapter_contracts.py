import asyncio
import base64
import json
import socket
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
from aiohttp import web

from jianer import common, events, segments
from jianer.LecAdapters.Feishu import Actions as FeishuActions
from jianer.LecAdapters.Kritor import Actions as KritorActions
from jianer.LecAdapters.Milky import Actions as MilkyActions
from jianer.LecAdapters.MilkyLib.translator import MilkyHttpConnection, msg_enid
from jianer.LecAdapters.OneBot import Actions as OneBotActions
from jianer.adapters.contracts import (
    Capability,
    ConversationKey,
    ConversationKind,
    MediaKind,
    MediaPolicy,
    MediaRequest,
    MediaSourceKind,
    ResolutionErrorCode,
    ResolutionStatus,
    normalize_external_id,
)
from jianer.adapters.media import resolve_media_request
import jianer.adapters.media as media_module
import jianer.LecAdapters.Kritor as kritor_module
from jianer.utils import errors


PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


def _conversation(protocol, conversation_id="10001", kind=ConversationKind.GROUP):
    return ConversationKey(
        protocol=protocol,
        self_id="90001",
        kind=kind,
        conversation_id=conversation_id,
        preset="normal",
    )


def test_external_id_and_conversation_key_are_canonical_strings():
    assert normalize_external_id(12345) == "12345"
    with pytest.raises(ValueError):
        normalize_external_id(None)
    with pytest.raises(ValueError):
        normalize_external_id(True)

    key = ConversationKey("OneBot", 90001, "group", 10001, " normal ")
    assert key.protocol == "onebot"
    assert key.self_id == "90001"
    assert key.conversation_id == "10001"
    assert key.preset == "normal"


def test_events_expose_string_ids_and_transport_conversation(monkeypatch):
    monkeypatch.setattr(
        events,
        "config",
        SimpleNamespace(
            protocol="OneBot",
            owner=[12345],
            black_list=[],
            silents=[],
        ),
        raising=False,
    )
    monkeypatch.setattr(
        events,
        "logger",
        SimpleNamespace(log=lambda *args, **kwargs: None, trace=lambda *args, **kwargs: None),
        raising=False,
    )
    event = events.GroupMessageEvent({
        "protocol": "OneBot",
        "post_type": "message",
        "message_type": "group",
        "sub_type": "normal",
        "time": 1,
        "self_id": 90001,
        "user_id": 12345,
        "group_id": 10001,
        "message_id": 42,
        "message": [{"type": "text", "data": {"text": "hello"}}],
        "sender": {
            "user_id": 12345,
            "nickname": "tester",
            "sex": "unknown",
            "age": 0,
            "card": "",
            "area": "",
            "level": "",
            "role": "member",
            "title": "",
        },
        "anonymous": None,
    })

    assert event.protocol == "onebot"
    assert event.self_id == "90001"
    assert event.user_id == "12345"
    assert event.sender.user_id == "12345"
    assert event.group_id == "10001"
    assert event.message_id == "42"
    assert ConversationKey.from_event(event, "normal") == _conversation("onebot")


def test_notice_and_request_ids_are_normalized_but_flags_remain_opaque(monkeypatch):
    monkeypatch.setattr(
        events,
        "config",
        SimpleNamespace(protocol="OneBot", owner=[], black_list=[], silents=[]),
        raising=False,
    )
    monkeypatch.setattr(
        events,
        "logger",
        SimpleNamespace(
            log=lambda *args, **kwargs: None,
            info=lambda *args, **kwargs: None,
            trace=lambda *args, **kwargs: None,
        ),
        raising=False,
    )
    base = {
        "protocol": "onebot",
        "post_type": "notice",
        "notice_type": "test",
        "time": 1,
        "self_id": 90001,
        "user_id": 12345,
        "group_id": 10001,
    }

    assert events.GroupAnonymous({"id": 7, "name": "anon", "flag": 88}).id == "7"
    for event_class in (
        events.GroupMemberDecreaseEvent,
        events.GroupMemberIncreaseEvent,
        events.GroupMuteEvent,
    ):
        event = event_class({**base, "operator_id": 54321})
        assert event.operator_id == "54321"

    group_recall = events.GroupRecallEvent({
        **base,
        "operator_id": 54321,
        "message_id": 42,
    })
    assert (group_recall.operator_id, group_recall.message_id) == ("54321", "42")
    friend_recall = events.FriendRecallEvent({**base, "message_id": 43})
    assert friend_recall.message_id == "43"
    notify = events.NotifyEvent({**base, "target_id": 67890})
    assert notify.target_id == "67890"
    essence = events.GroupEssenceEvent({
        **base,
        "sender_id": 12345,
        "operator_id": 54321,
        "message_id": 44,
    })
    assert (essence.sender_id, essence.operator_id, essence.message_id) == (
        "12345",
        "54321",
        "44",
    )
    reaction = events.MessageReactionEvent({
        **base,
        "operator_id": 54321,
        "message_id": 45,
    })
    assert (reaction.operator_id, reaction.message_id) == ("54321", "45")
    menu = events.BotMenuEvent({**base, "operator_id": 54321})
    assert menu.operator_id == "54321"

    request = events.RequestEvent({
        **base,
        "post_type": "request",
        "request_type": "friend",
        "flag": 987654321,
    })
    assert request.flag == 987654321


def test_kritor_notice_allows_an_omitted_operator_id(monkeypatch):
    monkeypatch.setattr(
        events,
        "config",
        SimpleNamespace(protocol="kritor", owner=[], black_list=[], silents=[]),
        raising=False,
    )
    monkeypatch.setattr(
        events,
        "logger",
        SimpleNamespace(log=lambda *args, **kwargs: None, trace=lambda *args, **kwargs: None),
        raising=False,
    )
    event = events.GroupMemberIncreaseEvent({
        "protocol": "kritor",
        "post_type": "notice",
        "notice_type": "group_increase",
        "time": 1,
        "self_id": 90001,
        "user_id": 12345,
        "group_id": 10001,
        "operator_id": None,
    })

    assert event.operator_id is None


def test_capability_sets_are_immutable_and_feishu_does_not_overclaim():
    assert isinstance(OneBotActions.capabilities, frozenset)
    assert isinstance(MilkyActions.capabilities, frozenset)
    assert Capability.RESOLVE_REFERENCE in OneBotActions.capabilities
    assert Capability.RESOLVE_MEDIA in MilkyActions.capabilities
    assert Capability.RESOLVE_REFERENCE in FeishuActions.capabilities
    assert Capability.RESOLVE_MEDIA not in FeishuActions.capabilities
    assert Capability.NATIVE_GROUP_FORWARD not in FeishuActions.capabilities
    assert Capability.RESOLVE_FORWARD not in FeishuActions.capabilities
    assert KritorActions.protocol == "kritor"
    assert KritorActions.capabilities == frozenset({Capability.SEND_REPLY})


@pytest.mark.parametrize(
    ("method_name", "args"),
    [
        ("send_forward_msg", (common.Message(segments.Text("node")),)),
        ("get_forward_msg", ("forward-id",)),
        (
            "send_group_forward_msg",
            ("oc_chat", common.Message(segments.Text("node"))),
        ),
    ],
)
def test_feishu_native_forward_apis_are_explicitly_unsupported(
        method_name,
        args,
):
    actions = FeishuActions(SimpleNamespace())

    with pytest.raises(errors.NotSupportError):
        asyncio.run(getattr(actions, method_name)(*args))


def test_kritor_get_msg_mapping_accepts_external_string_ids(monkeypatch):
    raw_message_id = "g0000000000000001000142"
    monkeypatch.setattr(
        kritor_module,
        "message_ids",
        {raw_message_id: 7},
    )

    assert KritorActions._mapped_message_id("7") == raw_message_id
    assert KritorActions._mapped_message_id(7) == raw_message_id
    with pytest.raises(errors.ArgsInvalidError):
        KritorActions._mapped_message_id("not-decimal")
    with pytest.raises(errors.ArgsInvalidError):
        KritorActions._mapped_message_id("8")

    class MappingReached(Exception):
        pass

    def mapped_from_get_msg(message_id):
        assert message_id == "7"
        raise MappingReached

    monkeypatch.setattr(
        KritorActions,
        "_mapped_message_id",
        staticmethod(mapped_from_get_msg),
    )
    with pytest.raises(MappingReached):
        asyncio.run(KritorActions(SimpleNamespace()).get_msg("7"))


def test_kritor_handler_stamps_protocol_and_conversation(monkeypatch):
    captured = []

    async def capture(event, _actions):
        captured.append(event)

    monkeypatch.setattr(kritor_module, "handler", capture)
    monkeypatch.setattr(
        events,
        "config",
        SimpleNamespace(protocol="kritor", owner=[], black_list=[], silents=[]),
        raising=False,
    )
    monkeypatch.setattr(
        events,
        "logger",
        SimpleNamespace(log=lambda *args, **kwargs: None, trace=lambda *args, **kwargs: None),
        raising=False,
    )
    kritor_module._handler({
        "post_type": "message",
        "message_type": "group",
        "sub_type": "normal",
        "time": 1,
        "self_id": 90001,
        "user_id": 12345,
        "group_id": 10001,
        "message_id": "g1",
        "message": [{"type": "text", "data": {"text": "hello"}}],
        "sender": {"user_id": 12345},
        "anonymous": None,
    }, SimpleNamespace())

    assert captured[0].protocol == "kritor"
    assert captured[0].conversation_id == "10001"


def test_onebot_numeric_boundary_rejects_foreign_and_boolean_ids():
    assert OneBotActions._numeric_id("12345", "user_id") == 12345
    with pytest.raises(errors.ArgsInvalidError):
        OneBotActions._numeric_id("ou_feishu", "user_id")
    with pytest.raises(errors.ArgsInvalidError):
        OneBotActions._numeric_id(True, "user_id")


def test_onebot_reference_resolution_validates_group(monkeypatch):
    actions = OneBotActions(None)

    async def fake_get_msg(message_id):
        assert message_id == "42"
        return SimpleNamespace(
            status="ok",
            ret_code=0,
            data=SimpleNamespace(
                raw={
                    "time": 1710000000,
                    "message_type": "group",
                    "group_id": 10001,
                    "sender": {"user_id": 12345},
                },
                message=common.Message(segments.Text("quoted")),
            ),
        )

    monkeypatch.setattr(actions, "get_msg", fake_get_msg)
    result = asyncio.run(actions.resolve_reference(
        "42",
        conversation=_conversation("onebot"),
    ))

    assert result.status is ResolutionStatus.OK
    assert result.message_id == "42"
    assert result.sender_id == "12345"
    assert str(common.Message(*result.segments)) == "quoted"


def test_onebot_reference_timeout_has_stable_result(monkeypatch):
    actions = OneBotActions(None)

    async def slow_get_msg(_message_id):
        await asyncio.sleep(0.05)

    monkeypatch.setattr(actions, "get_msg", slow_get_msg)
    result = asyncio.run(actions.resolve_reference(
        "42",
        conversation=_conversation("onebot"),
        timeout_seconds=0.001,
    ))

    assert result.status is ResolutionStatus.ERROR
    assert result.error_code is ResolutionErrorCode.TIMEOUT


def test_reference_timeout_must_be_positive(monkeypatch):
    actions = OneBotActions(None)

    async def unexpected_get_msg(_message_id):
        raise AssertionError("invalid timeout must be rejected before fetch")

    monkeypatch.setattr(actions, "get_msg", unexpected_get_msg)
    result = asyncio.run(actions.resolve_reference(
        "42",
        conversation=_conversation("onebot"),
        timeout_seconds=0,
    ))

    assert result.status is ResolutionStatus.REJECTED
    assert result.error_code is ResolutionErrorCode.INVALID_TIMEOUT


def test_milky_reference_rejects_cross_conversation_without_upstream_call(monkeypatch):
    actions = MilkyActions(MilkyHttpConnection("ws://127.0.0.1:3000"))

    async def unexpected_get_msg(message_id):
        raise AssertionError("cross-conversation references must be rejected before fetch")

    monkeypatch.setattr(actions, "get_msg", unexpected_get_msg)
    result = asyncio.run(actions.resolve_reference(
        str(msg_enid(1, 42, 20002)),
        conversation=_conversation("milky", "10001"),
    ))

    assert result.status is ResolutionStatus.REJECTED
    assert result.error_code is ResolutionErrorCode.CONVERSATION_MISMATCH


def test_feishu_reference_is_structural_and_conversation_checked():
    class Client:
        def get_message(self, message_id):
            assert message_id == "om_1"
            return {
                "message_id": message_id,
                "chat_type": "group",
                "chat_id": "oc_chat",
                "message_type": "text",
                "content": json.dumps({"text": "quoted"}),
                "create_time": "1710000000000",
                "sender": {"sender_id": {"open_id": "ou_sender"}},
            }

    actions = FeishuActions(Client())
    result = asyncio.run(actions.resolve_reference(
        "om_1",
        conversation=_conversation("feishu", "oc_chat"),
    ))

    assert result.status is ResolutionStatus.OK
    assert result.sender_id == "ou_sender"
    assert result.sent_at == 1710000000
    assert isinstance(result.segments[0], segments.Text)
    assert result.segments[0].text == "quoted"

    mismatch = asyncio.run(actions.resolve_reference(
        "om_1",
        conversation=_conversation("feishu", "oc_other"),
    ))
    assert mismatch.status is ResolutionStatus.REJECTED
    assert mismatch.error_code is ResolutionErrorCode.CONVERSATION_MISMATCH


def test_feishu_media_resolution_is_explicitly_unsupported():
    request = MediaRequest(
        MediaSourceKind.ADAPTER_RESOURCE,
        MediaKind.IMAGE,
        "img_opaque",
        message_id="om_1",
    )
    result = asyncio.run(FeishuActions(SimpleNamespace()).resolve_media(
        request,
        conversation=_conversation("feishu", "oc_chat"),
    ))

    assert result.status is ResolutionStatus.UNSUPPORTED
    assert result.error_code is ResolutionErrorCode.CAPABILITY_UNAVAILABLE
    assert result.data is None
    assert "img_opaque" not in result.source


def test_data_media_resolution_sniffs_mime_and_hides_payload():
    encoded = base64.b64encode(PNG_BYTES).decode("ascii")
    locator = f"data:image/png;base64,{encoded}"
    request = MediaRequest(MediaSourceKind.DATA_URI, MediaKind.IMAGE, locator)

    assert locator not in repr(request)
    result = asyncio.run(resolve_media_request(request, MediaPolicy()))

    assert result.status is ResolutionStatus.OK
    assert result.mime == "image/png"
    assert result.size == len(PNG_BYTES)
    assert result.data == PNG_BYTES
    assert result.source == "data:image/png"


def test_media_kind_rejects_a_mismatched_sniffed_family():
    encoded = base64.b64encode(PNG_BYTES).decode("ascii")
    request = MediaRequest(
        MediaSourceKind.DATA_URI,
        MediaKind.AUDIO,
        f"data:image/png;base64,{encoded}",
    )

    result = asyncio.run(resolve_media_request(request, MediaPolicy()))

    assert result.status is ResolutionStatus.REJECTED
    assert result.error_code is ResolutionErrorCode.MIME_MISMATCH


@pytest.mark.parametrize(
    ("media_kind", "mime", "payload"),
    [
        (MediaKind.AUDIO, "audio/mpeg", b"ID3\x04\x00\x00\x00\x00\x00\x00"),
        (MediaKind.AUDIO, "audio/wav", b"RIFF\x10\x00\x00\x00WAVEfmt "),
        (MediaKind.AUDIO, "audio/ogg", b"OggS" + b"\x00" * 24 + b"OpusHead"),
        (MediaKind.AUDIO, "audio/flac", b"fLaC\x00\x00\x00\x22"),
        (MediaKind.VIDEO, "video/mp4", b"\x00\x00\x00\x18ftypisom\x00\x00\x00\x00"),
        (MediaKind.VIDEO, "video/webm", b"\x1a\x45\xdf\xa3\x42\x82\x84webm"),
    ],
)
def test_explicitly_allowlisted_audio_and_video_are_sniffed(
        media_kind,
        mime,
        payload,
):
    encoded = base64.b64encode(payload).decode("ascii")
    request = MediaRequest(
        MediaSourceKind.DATA_URI,
        media_kind,
        f"data:{mime};base64,{encoded}",
    )
    policy = MediaPolicy(allowed_mime_types=frozenset({mime}))

    result = asyncio.run(resolve_media_request(request, policy))

    assert result.status is ResolutionStatus.OK
    assert result.mime == mime
    assert result.data == payload


def test_media_resolution_rejects_mime_spoof_and_unlisted_remote_origin():
    encoded = base64.b64encode(PNG_BYTES).decode("ascii")
    spoofed = MediaRequest(
        MediaSourceKind.DATA_URI,
        MediaKind.IMAGE,
        f"data:text/html;base64,{encoded}",
    )
    spoofed_result = asyncio.run(resolve_media_request(spoofed, MediaPolicy()))
    assert spoofed_result.error_code is ResolutionErrorCode.MIME_MISMATCH

    remote = MediaRequest(
        MediaSourceKind.REMOTE_URL,
        MediaKind.IMAGE,
        "https://example.test/image.png?token=secret",
    )
    remote_result = asyncio.run(resolve_media_request(remote, MediaPolicy()))
    assert remote_result.status is ResolutionStatus.REJECTED
    assert remote_result.error_code is ResolutionErrorCode.ORIGIN_NOT_ALLOWED
    assert "token" not in remote_result.source
    assert "secret" not in remote_result.source


def test_media_resolution_rejects_invalid_base64_and_decoded_size_limit():
    invalid = MediaRequest(
        MediaSourceKind.DATA_URI,
        MediaKind.IMAGE,
        "data:image/png;base64,not-valid-@@",
    )
    invalid_result = asyncio.run(resolve_media_request(invalid, MediaPolicy()))
    assert invalid_result.error_code is ResolutionErrorCode.DECODE_FAILED

    encoded = base64.b64encode(PNG_BYTES).decode("ascii")
    oversized = MediaRequest(
        MediaSourceKind.DATA_URI,
        MediaKind.IMAGE,
        f"data:image/png;base64,{encoded}",
    )
    oversized_result = asyncio.run(resolve_media_request(
        oversized,
        MediaPolicy(max_bytes=len(PNG_BYTES) - 1),
    ))
    assert oversized_result.error_code is ResolutionErrorCode.TOO_LARGE


def test_local_media_is_default_deny_then_allowlisted(tmp_path):
    image = tmp_path / "image.png"
    image.write_bytes(PNG_BYTES)
    request = MediaRequest(MediaSourceKind.LOCAL_FILE, MediaKind.IMAGE, str(image))

    denied = asyncio.run(resolve_media_request(request, MediaPolicy()))
    allowed = asyncio.run(resolve_media_request(
        request,
        MediaPolicy(allowed_local_roots=(tmp_path,)),
    ))

    assert denied.error_code is ResolutionErrorCode.LOCAL_PATH_DENIED
    assert allowed.status is ResolutionStatus.OK
    assert allowed.source == "local:image.png"


def test_local_file_uri_source_uses_decoded_path_without_query_or_fragment(tmp_path):
    image = tmp_path / "private image.png"
    image.write_bytes(PNG_BYTES)
    locator = f"{image.resolve().as_uri()}?access_token=secret#private-fragment"
    request = MediaRequest(MediaSourceKind.LOCAL_FILE, MediaKind.IMAGE, locator)

    result = asyncio.run(resolve_media_request(
        request,
        MediaPolicy(allowed_local_roots=(tmp_path,)),
    ))

    assert result.status is ResolutionStatus.OK
    assert result.source == "local:private image.png"
    assert "access_token" not in result.source
    assert "secret" not in result.source
    assert "fragment" not in result.source


def test_denied_file_uri_source_does_not_leak_query_or_fragment(tmp_path):
    locator = (
        "file://server/share/private%20image.png"
        "?access_token=secret#private-fragment"
    )
    request = MediaRequest(MediaSourceKind.LOCAL_FILE, MediaKind.IMAGE, locator)

    result = asyncio.run(resolve_media_request(
        request,
        MediaPolicy(allowed_local_roots=(tmp_path,)),
    ))

    assert result.error_code is ResolutionErrorCode.LOCAL_PATH_DENIED
    assert result.source == "local:private image.png"
    assert "access_token" not in result.source
    assert "secret" not in result.source
    assert "fragment" not in result.source


def test_local_media_rejects_root_escape_remote_file_host_and_size(tmp_path):
    allowed_root = tmp_path / "allowed"
    allowed_root.mkdir()
    outside = tmp_path / "outside.png"
    outside.write_bytes(PNG_BYTES)

    escaped = MediaRequest(MediaSourceKind.LOCAL_FILE, MediaKind.IMAGE, str(outside))
    escaped_result = asyncio.run(resolve_media_request(
        escaped,
        MediaPolicy(allowed_local_roots=(allowed_root,)),
    ))
    assert escaped_result.error_code is ResolutionErrorCode.LOCAL_PATH_DENIED

    remote_file = MediaRequest(
        MediaSourceKind.LOCAL_FILE,
        MediaKind.IMAGE,
        "file://server/share/image.png",
    )
    remote_file_result = asyncio.run(resolve_media_request(
        remote_file,
        MediaPolicy(allowed_local_roots=(allowed_root,)),
    ))
    assert remote_file_result.error_code is ResolutionErrorCode.LOCAL_PATH_DENIED

    inside = allowed_root / "large.png"
    inside.write_bytes(PNG_BYTES)
    too_large_result = asyncio.run(resolve_media_request(
        MediaRequest(MediaSourceKind.LOCAL_FILE, MediaKind.IMAGE, str(inside)),
        MediaPolicy(max_bytes=len(PNG_BYTES) - 1, allowed_local_roots=(allowed_root,)),
    ))
    assert too_large_result.error_code is ResolutionErrorCode.TOO_LARGE


def test_remote_media_blocks_private_ssrf_for_public_allowlist():
    request = MediaRequest(
        MediaSourceKind.REMOTE_URL,
        MediaKind.IMAGE,
        "http://localhost:32109/image.png",
    )
    result = asyncio.run(resolve_media_request(
        request,
        MediaPolicy(allowed_remote_origins=frozenset({"http://localhost:32109"})),
    ))

    assert result.status is ResolutionStatus.REJECTED
    assert result.error_code is ResolutionErrorCode.ORIGIN_NOT_ALLOWED


def test_remote_media_redirect_mime_and_size_policy(monkeypatch):
    async def scenario():
        app = web.Application()

        async def image(_request):
            return web.Response(body=PNG_BYTES, content_type="image/png")

        async def html(_request):
            return web.Response(body=b"<html>not an image</html>", content_type="text/html")

        async def stream_large(request):
            response = web.StreamResponse(
                status=200,
                headers={"Content-Type": "image/png"},
            )
            await response.prepare(request)
            await response.write(PNG_BYTES[:12])
            await response.write(PNG_BYTES[12:])
            await response.write_eof()
            return response

        async def loop_redirect(_request):
            raise web.HTTPFound("/loop")

        async def disallowed_redirect(_request):
            raise web.HTTPFound("http://127.0.0.1:9/image.png")

        app.router.add_get("/image", image)
        app.router.add_get("/html", html)
        app.router.add_get("/large", stream_large)
        app.router.add_get("/loop", loop_redirect)
        app.router.add_get("/escape", disallowed_redirect)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        origin = f"http://127.0.0.1:{port}"
        real_validate = media_module._validate_remote_target

        async def validate_test_server(url, policy):
            parsed = urlsplit(url)
            if parsed.hostname == "127.0.0.1" and parsed.port == port:
                return (
                    origin,
                    "127.0.0.1",
                    port,
                    [(
                        socket.AF_INET,
                        socket.SOCK_STREAM,
                        socket.IPPROTO_TCP,
                        "",
                        ("127.0.0.1", port),
                    )],
                )
            return await real_validate(url, policy)

        monkeypatch.setattr(media_module, "_validate_remote_target", validate_test_server)
        policy = MediaPolicy(
            allowed_remote_origins=frozenset({origin}),
            max_redirects=1,
        )
        try:
            ok = await resolve_media_request(
                MediaRequest(MediaSourceKind.REMOTE_URL, MediaKind.IMAGE, f"{origin}/image"),
                policy,
            )
            wrong_mime = await resolve_media_request(
                MediaRequest(MediaSourceKind.REMOTE_URL, MediaKind.IMAGE, f"{origin}/html"),
                policy,
            )
            redirect_loop = await resolve_media_request(
                MediaRequest(MediaSourceKind.REMOTE_URL, MediaKind.IMAGE, f"{origin}/loop"),
                policy,
            )
            redirect_escape = await resolve_media_request(
                MediaRequest(MediaSourceKind.REMOTE_URL, MediaKind.IMAGE, f"{origin}/escape"),
                policy,
            )
            streamed_too_large = await resolve_media_request(
                MediaRequest(MediaSourceKind.REMOTE_URL, MediaKind.IMAGE, f"{origin}/large"),
                MediaPolicy(
                    max_bytes=len(PNG_BYTES) - 1,
                    allowed_remote_origins=frozenset({origin}),
                ),
            )
        finally:
            await runner.cleanup()
        return ok, wrong_mime, redirect_loop, redirect_escape, streamed_too_large

    ok, wrong_mime, redirect_loop, redirect_escape, streamed_too_large = asyncio.run(scenario())
    assert ok.status is ResolutionStatus.OK
    assert ok.mime == "image/png"
    assert wrong_mime.error_code is ResolutionErrorCode.MIME_UNSUPPORTED
    assert redirect_loop.error_code is ResolutionErrorCode.REDIRECT_LIMIT
    assert redirect_escape.error_code is ResolutionErrorCode.ORIGIN_NOT_ALLOWED
    assert streamed_too_large.error_code is ResolutionErrorCode.TOO_LARGE

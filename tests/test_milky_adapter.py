import asyncio
import pytest

from jianer import common
from jianer.LecAdapters.Milky import Actions
from jianer.LecAdapters.MilkyLib import translator
from jianer.LecAdapters.MilkyLib.Manager import Packet, reports
from jianer.LecAdapters.MilkyLib.translator import (
    MilkyHttpConnection,
    MilkyOutGoingSegBuilder,
    message_translator,
    msg_enid,
    normalize_uri,
    prepare_outgoing_media_uri,
)
from jianer.LecAdapters.MilkyLib.types import consume_milky_event, make_reply_segment, make_text_segment
from jianer.utils import errors


class _MilkySegment:
    def __init__(self, payload):
        self.payload = payload

    def milky_outgoing_seg(self):
        return self.payload

    def __str__(self):
        return str(self.payload)


def test_milky_message_translator_accepts_common_field_variants():
    message = message_translator(
        [
            {"type": "text", "data": {"text": "hello"}},
            {"type": "image", "data": {"url": "https://example.test/a.png"}},
            {"type": "mention", "data": {"user_id": 10001}},
            {"type": "reply", "data": {"message_seq": 42}},
            {"type": "forward", "data": {"id": "forward-1"}},
        ],
        peer_id=20002,
        scene=1,
    )

    assert message[0] == {"type": "text", "data": {"text": "hello"}}
    assert message[1]["data"]["file"] == "https://example.test/a.png"
    assert message[2] == {"type": "at", "data": {"qq": "10001"}}
    assert message[3] == {
        "type": "reply",
        "data": {"id": str(msg_enid(1, 42, 20002))},
    }
    assert message[4] == {"type": "forward", "data": {"id": "forward-1"}}


def test_milky_event_can_be_unwrapped_from_body_packet():
    event = consume_milky_event({
        "body": {
            "type": "message_receive",
            "time": "1710000000",
            "self_id": "12345",
            "data": {"message_scene": "group"},
        }
    })

    assert event["type"] == "message_receive"
    assert event["time"] == 1710000000
    assert event["self_id"] == 12345


def test_milky_packet_stores_echoed_response(monkeypatch):
    connection = MilkyHttpConnection("ws://127.0.0.1:3000")

    def fake_http_send(endpoint, data):
        return {"status": "ok", "retcode": 0, "data": {"endpoint": endpoint, "payload": data}}

    monkeypatch.setattr(connection, "http_send", fake_http_send)
    packet = Packet("demo_endpoint", value=1)

    response = packet.send_to(connection)
    fetched = reports.get(packet.echo)

    assert response["echo"] == packet.echo
    assert fetched["data"] == {"endpoint": "demo_endpoint", "payload": {"value": 1}}


def test_milky_normalize_uri_keeps_remote_urls():
    assert normalize_uri("https://example.test/file.png") == "https://example.test/file.png"


def test_milky_normalize_uri_handles_windows_drive_paths():
    assert normalize_uri("D:\\SRInternet.SR\\JianerCore\\ban.png") == (
        "file:///D:/SRInternet.SR/JianerCore/ban.png"
    )


def test_milky_prepare_outgoing_media_uri_resolves_bare_local_file(tmp_path, monkeypatch):
    image = tmp_path / "image.bin"
    image.write_bytes(b"test-image")
    monkeypatch.chdir(tmp_path)

    uri = prepare_outgoing_media_uri("image.bin")

    assert uri.startswith("file:///")
    assert uri.endswith("/image.bin")


def test_milky_prepare_outgoing_media_uri_rejects_missing_file():
    with pytest.raises(FileNotFoundError, match="Milky media file does not exist"):
        prepare_outgoing_media_uri("missing-local-image.png")


def test_milky_prepare_outgoing_media_uri_rejects_unknown_scheme():
    with pytest.raises(ValueError, match="Unsupported Milky media URI scheme"):
        prepare_outgoing_media_uri("ftp://example.test/image.png")


def test_milky_outgoing_builder_uses_normalized_media_uri(tmp_path):
    image = tmp_path / "image.bin"
    image.write_bytes(b"test-image")

    segment = MilkyOutGoingSegBuilder().image(str(image)).build()[0]

    assert segment["type"] == "image"
    assert segment["data"]["uri"].startswith("file:///")
    assert segment["data"]["uri"].endswith("/image.bin")


def test_milky_send_plain_text_returns_response(monkeypatch):
    connection = MilkyHttpConnection("ws://127.0.0.1:3000")
    calls = []

    def fake_http_send(endpoint, data):
        calls.append((endpoint, data))
        return {"status": "ok", "retcode": 0, "data": {"message_seq": 1}}

    monkeypatch.setattr(connection, "http_send", fake_http_send)
    actions = Actions(connection)

    response = asyncio.run(actions.send("hello", group_id=10001))

    assert response.ret_code == 0
    assert response.data.message_id == msg_enid(1, 1, 10001)
    assert calls == [
        (
            "send_group_message",
            {"group_id": 10001, "message": [{"type": "text", "data": {"text": "hello"}}]},
        )
    ]


def test_milky_send_rejects_empty_message_before_http_call(monkeypatch):
    connection = MilkyHttpConnection("ws://127.0.0.1:3000")
    calls = []
    monkeypatch.setattr(connection, "http_send", lambda endpoint, data: calls.append((endpoint, data)))

    with pytest.raises(errors.ArgsInvalidError, match="at least one supported segment"):
        asyncio.run(Actions(connection).send(common.Message(), group_id=10001))

    assert calls == []


def test_milky_send_retries_without_reply_when_reply_payload_rejected(monkeypatch):
    connection = MilkyHttpConnection("ws://127.0.0.1:3000")
    calls = []

    def fake_http_send(endpoint, data):
        calls.append((endpoint, data))
        if len(calls) == 1:
            return {"status": "failed", "retcode": 400, "data": None}
        return {"status": "ok", "retcode": 0, "data": {"message_seq": 2}}

    monkeypatch.setattr(connection, "http_send", fake_http_send)
    actions = Actions(connection)
    message = common.Message(
        _MilkySegment(make_reply_segment(123)),
        _MilkySegment(make_text_segment("hello")),
    )

    response = asyncio.run(actions.send(message, group_id=10001))

    assert response.ret_code == 0
    assert response.data.message_id == msg_enid(1, 2, 10001)
    assert calls[0][1]["message"][0] == {"type": "reply", "data": {"message_seq": 123}}
    assert calls[1][1]["message"] == [{"type": "text", "data": {"text": "hello"}}]


def test_milky_send_raises_and_logs_api_failure(monkeypatch):
    connection = MilkyHttpConnection("ws://127.0.0.1:3000")
    failures = []

    monkeypatch.setattr(
        connection,
        "http_send",
        lambda endpoint, data: {
            "status": "failed",
            "retcode": 500,
            "message": "message payload cannot be parsed",
            "data": None,
        },
    )
    monkeypatch.setattr("jianer.LecAdapters.Milky.logger.error", failures.append)

    with pytest.raises(errors.ActionFailedError, match="Milky send failed"):
        asyncio.run(Actions(connection).send("hello", group_id=10001))

    assert len(failures) == 1
    assert "向群 10001发送失败" in failures[0]
    assert "send_group_message" in failures[0]


def test_milky_get_stranger_info_uses_standard_profile_endpoint(monkeypatch):
    connection = MilkyHttpConnection("ws://127.0.0.1:3000")
    calls = []

    def fake_http_send(endpoint, data):
        calls.append((endpoint, data))
        return {"status": "ok", "retcode": 0, "data": {"name": "Tom"}}

    monkeypatch.setattr(connection, "http_send", fake_http_send)

    response = asyncio.run(Actions(connection).get_stranger_info(10001))

    assert response.ret_code == 0
    assert response.data.user_id == 10001
    assert response.data.nickname == "Tom"
    assert calls == [("get_user_profile", {"user_id": 10001})]


def test_milky_http_send_uses_http_api_and_auth_header(monkeypatch):
    class Response:
        status_code = 200
        text = '{"status":"ok"}'

        def json(self):
            return {"status": "ok", "retcode": 0, "data": {"message_seq": 1}}

    calls = []

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        return Response()

    monkeypatch.setattr(translator.httpx, "post", fake_post)
    connection = MilkyHttpConnection("ws://127.0.0.1:3000", auth="secret")

    response = connection.http_send("send_group_message", {"group_id": 1, "message": []})

    assert response["status"] == "ok"
    assert calls == [
        (
            "http://127.0.0.1:3000/api/send_group_message",
            {
                "json": {"group_id": 1, "message": []},
                "headers": {"Authorization": "Bearer secret"},
                "timeout": 15.0,
            },
        )
    ]


def test_milky_http_send_reports_non_json_response(monkeypatch):
    class Response:
        status_code = 502
        text = "bad gateway"

        def json(self):
            raise translator.json.JSONDecodeError("bad json", self.text, 0)

    monkeypatch.setattr(translator.httpx, "post", lambda *args, **kwargs: Response())
    connection = MilkyHttpConnection("ws://127.0.0.1:3000")

    response = connection.http_send("send_group_message", {"group_id": 1, "message": []})

    assert response["status"] == "failed"
    assert response["retcode"] == 502
    assert response["data"]["raw"] == "bad gateway"


def test_milky_http_send_retries_transient_non_json_502(monkeypatch):
    class Response:
        def __init__(self, status_code, text, payload=None):
            self.status_code = status_code
            self.text = text
            self.payload = payload

        def json(self):
            if self.payload is None:
                raise translator.json.JSONDecodeError("bad json", self.text, 0)
            return self.payload

    responses = [
        Response(502, ""),
        Response(502, ""),
        Response(200, '{"status":"ok"}', {"status": "ok", "retcode": 0, "data": {}}),
    ]
    monkeypatch.setattr(translator.httpx, "post", lambda *args, **kwargs: responses.pop(0))
    monkeypatch.setattr(translator.time, "sleep", lambda seconds: None)
    connection = MilkyHttpConnection("ws://127.0.0.1:3000")

    response = connection.http_send("send_group_message", {"group_id": 1, "message": []})

    assert response["status"] == "ok"
    assert response["retcode"] == 0
    assert responses == []


def test_milky_http_send_marks_json_http_errors_as_failed(monkeypatch):
    class Response:
        status_code = 401
        text = '{"message":"unauthorized"}'
        is_success = False

        def json(self):
            return {"message": "unauthorized"}

    monkeypatch.setattr(translator.httpx, "post", lambda *args, **kwargs: Response())
    connection = MilkyHttpConnection("ws://127.0.0.1:3000")

    response = connection.http_send("send_group_message", {"group_id": 1, "message": []})

    assert response["status"] == "failed"
    assert response["retcode"] == 401
    assert response["message"] == "unauthorized"

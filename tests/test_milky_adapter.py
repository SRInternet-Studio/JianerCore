import asyncio

import pytest

from jianer.LecAdapters.Milky import Actions
from jianer.LecAdapters.MilkyLib.Manager import Packet, reports
from jianer.LecAdapters.MilkyLib.translator import (
    MilkyHttpConnection,
    MilkyOutGoingSegBuilder,
    message_translator,
    msg_enid,
    normalize_uri,
)
from jianer.LecAdapters.MilkyLib.types import consume_milky_event
from jianer.utils import errors


def test_milky_message_translator_accepts_common_field_variants():
    message = message_translator(
        [
            {"type": "text", "data": {"text": "hello"}},
            {"type": "image", "data": {"url": "https://example.test/a.png"}},
            {"type": "mention", "data": {"user_id": 10001}},
            {"type": "reply", "data": {"message_seq": 42}},
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


def test_milky_normalize_uri_fixes_windows_file_urls():
    assert normalize_uri("file://D:\\SRInternet.SR\\JianerCore\\ban.png") == (
        "file:///D:/SRInternet.SR/JianerCore/ban.png"
    )


def test_milky_image_segment_normalizes_windows_file_url():
    segment = MilkyOutGoingSegBuilder().image("file://D:\\SRInternet.SR\\JianerCore\\ban.png").build()[0]

    assert segment["data"]["uri"] == "file:///D:/SRInternet.SR/JianerCore/ban.png"


def test_milky_send_raises_when_api_rejects(monkeypatch):
    connection = MilkyHttpConnection("ws://127.0.0.1:3000")

    def fake_http_send(endpoint, data):
        return {"status": "failed", "retcode": 400, "message": "bad payload", "data": None}

    monkeypatch.setattr(connection, "http_send", fake_http_send)
    actions = Actions(connection)

    with pytest.raises(errors.ActionFailedError, match="Milky send failed"):
        asyncio.run(actions.send("hello", group_id=10001))

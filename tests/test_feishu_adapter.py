import json
import asyncio

from jianer.LecAdapters.Feishu import Actions, _message_to_feishu
from jianer.LecAdapters.FeishuLib.Manager import Packet, reports
from jianer.LecAdapters.FeishuLib.client import (
    FeishuHttpConnection,
    feishu_content_to_onebot,
    translate_event,
)
from jianer import common, segments


def test_feishu_text_content_translates_mentions():
    message = feishu_content_to_onebot(
        "text",
        "{\"text\":\"@_user_1 hello\"}",
        [
            {
                "key": "@_user_1",
                "id": {"open_id": "ou_user"},
                "mentioned_type": "user",
            }
        ],
    )

    assert message == [
        {"type": "at", "data": {"qq": "ou_user"}},
        {"type": "text", "data": {"text": " hello"}},
    ]


def test_feishu_message_event_translates_to_group_message():
    payload = {
        "schema": "2.0",
        "header": {
            "event_id": "evt_1",
            "event_type": "im.message.receive_v1",
            "create_time": "1608725989000",
            "app_id": "cli_app",
        },
        "event": {
            "sender": {
                "sender_id": {"open_id": "ou_sender"},
                "sender_type": "user",
            },
            "message": {
                "message_id": "om_msg",
                "create_time": "1609073151345",
                "chat_id": "oc_chat",
                "chat_type": "group",
                "message_type": "text",
                "content": "{\"text\":\"hello\"}",
            },
        },
    }

    event = translate_event(payload, self_id="ou_bot")

    assert event["post_type"] == "message"
    assert event["message_type"] == "group"
    assert event["group_id"] == "oc_chat"
    assert event["user_id"] == "ou_sender"
    assert event["self_id"] == "ou_bot"
    assert event["message"] == [{"type": "text", "data": {"text": "hello"}}]


def test_feishu_menu_event_translates_to_notice():
    payload = {
        "schema": "2.0",
        "header": {
            "event_type": "application.bot.menu_v6",
            "app_id": "cli_app",
            "create_time": "1608725989000",
        },
        "event": {
            "operator": {
                "operator_name": "Tom",
                "operator_id": {"open_id": "ou_operator"},
            },
            "event_key": "menu_key",
            "timestamp": 1669364458,
        },
    }

    event = translate_event(payload, self_id="ou_bot")

    assert event["post_type"] == "notice"
    assert event["notice_type"] == "bot_menu"
    assert event["operator_id"] == "ou_operator"
    assert event["operator_name"] == "Tom"
    assert event["event_key"] == "menu_key"


def test_feishu_message_to_text_payload_supports_at_segments():
    msg_type, content = _message_to_feishu(
        common.Message(segments.Text("hi "), segments.At("ou_user"))
    )

    assert msg_type == "text"
    assert content == {"text": "hi <at id=\"ou_user\"></at>"}


def test_feishu_packet_stores_normalized_send_response(monkeypatch):
    connection = FeishuHttpConnection("cli_app", "secret", tenant_access_token="t-token")
    calls = []

    def fake_request(method, path, *, params=None, json_body=None, auth=True):
        calls.append((method, path, params, json_body, auth))
        return {
            "code": 0,
            "msg": "success",
            "data": {"message_id": "om_msg"},
        }

    monkeypatch.setattr(connection, "request", fake_request)
    packet = Packet(
        "send_message",
        receive_id="oc_chat",
        receive_id_type="chat_id",
        msg_type="text",
        content={"text": "hello"},
    )

    response = packet.send_to(connection)
    fetched = reports.get(packet.echo)

    assert response["status"] == "ok"
    assert fetched["data"]["message_id"] == "om_msg"
    assert calls == [
        (
            "POST",
            "/open-apis/im/v1/messages",
            {"receive_id_type": "chat_id"},
            {
                "receive_id": "oc_chat",
                "msg_type": "text",
                "content": json.dumps({"text": "hello"}, ensure_ascii=False),
            },
            True,
        )
    ]


def test_feishu_actions_send_returns_message_id(monkeypatch):
    connection = FeishuHttpConnection("cli_app", "secret", tenant_access_token="t-token")

    def fake_request(method, path, *, params=None, json_body=None, auth=True):
        return {
            "code": 0,
            "msg": "success",
            "data": {"message_id": "om_msg"},
        }

    monkeypatch.setattr(connection, "request", fake_request)

    result = asyncio.run(Actions(connection).send("hello", group_id="oc_chat"))

    assert result.status == "ok"
    assert result.data.message_id == "om_msg"

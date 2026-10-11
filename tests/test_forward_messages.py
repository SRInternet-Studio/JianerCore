import asyncio
import json
import threading

from jianer import common, segments
from jianer.LecAdapters import OneBot
from jianer.LecAdapters.Milky import Actions as MilkyActions
from jianer.LecAdapters.MilkyLib.translator import MilkyHttpConnection
from jianer.LecAdapters.OneBot import Actions as OneBotActions
from jianer.network import WebsocketConnection


class _FakeWebsocket(WebsocketConnection):
    def __init__(self, respond):
        self.respond = respond
        self.sent = []

    def send(self, message: str) -> None:
        payload = json.loads(message)
        self.sent.append(payload)
        threading.Timer(0.01, self.respond, args=(payload,)).start()


def _onebot_actions(respond):
    connection = _FakeWebsocket(respond)
    return OneBotActions(connection), connection


def _milky_actions(monkeypatch, responder):
    connection = MilkyHttpConnection("ws://127.0.0.1:3000")
    calls = []

    def fake_http_send(endpoint, data):
        calls.append((endpoint, data))
        return responder(endpoint, data)

    monkeypatch.setattr(connection, "http_send", fake_http_send)
    return MilkyActions(connection), calls


def test_custom_node_emits_onebot_nickname_key():
    node = segments.CustomNode("12345", "Alice", common.Message(segments.Text("hi")))

    data = node.to_json()["data"]

    # OneBot v11 reads `nickname`; the legacy `nick_name` alias is kept for older consumers.
    assert data["nickname"] == "Alice"
    assert data["nick_name"] == "Alice"
    assert data["user_id"] == "12345"


def test_node_segment_with_message_content_serializes():
    message = common.Message(segments.Node("12345", "Alice", common.Message(segments.Text("hi"))))

    assert message.get_sync() == [{
        "type": "node",
        "data": {
            "user_id": "12345",
            "nickname": "Alice",
            "content": [{"type": "text", "data": {"text": "hi"}}],
        },
    }]


def test_onebot_group_forward_carries_node_sender_name():
    def respond(payload):
        OneBot.reports.put(payload["echo"], {
            "status": "ok",
            "retcode": 0,
            "data": {"message_id": "1", "forward_id": "fwd"},
            "echo": payload["echo"],
        })

    actions, connection = _onebot_actions(respond)
    message = common.Message(segments.CustomNode("12345", "bot", common.Message(segments.Text("hello"))))

    asyncio.run(actions.send_group_forward_msg(10001, message))

    node = connection.sent[0]["params"]["messages"][0]
    assert node["data"]["nickname"] == "bot"
    assert node["data"]["user_id"] == "12345"


def test_onebot_get_forward_msg_reads_llbot_sender_object():
    def respond(payload):
        OneBot.reports.put(payload["echo"], {
            "status": "ok",
            "retcode": 0,
            "data": {"messages": [{
                "content": [{"type": "text", "data": {"text": "hello"}}],
                "sender": {"nickname": "tester", "user_id": 12345},
                "time": 1,
                "message_format": "array",
                "message_type": "group",
            }]},
            "echo": payload["echo"],
        })

    actions, _ = _onebot_actions(respond)

    ret = asyncio.run(actions.get_forward_msg("fwd-1"))

    node = ret.data[0]
    assert isinstance(node, segments.Node)
    assert node.nickname == "tester"
    assert node.user_id == "12345"
    assert str(node.content) == "hello"


def test_onebot_get_forward_msg_reads_standard_node_shape():
    def respond(payload):
        OneBot.reports.put(payload["echo"], {
            "status": "ok",
            "retcode": 0,
            "data": {"message": [{
                "type": "node",
                "data": {
                    "user_id": "10001",
                    "nickname": "Alice",
                    "content": [{"type": "text", "data": {"text": "hi"}}],
                },
            }]},
            "echo": payload["echo"],
        })

    actions, _ = _onebot_actions(respond)

    node = asyncio.run(actions.get_forward_msg("fwd-2")).data[0]

    assert node.nickname == "Alice"
    assert node.user_id == "10001"


def test_milky_get_forward_msg_falls_back_to_nickname(monkeypatch):
    actions, _ = _milky_actions(
        monkeypatch,
        lambda endpoint, data: {
            "status": "ok",
            "retcode": 0,
            "data": {"messages": [{
                "user_id": 12345,
                "nickname": "tester",
                "segments": [{"type": "text", "data": {"text": "hello"}}],
            }]},
        },
    )

    node = asyncio.run(actions.get_forward_msg("forward-1")).data[0]

    assert node.nickname == "tester"
    assert node.user_id == "12345"


def test_milky_forward_solve_resolves_forward_segment(monkeypatch):
    def responder(endpoint, data):
        assert endpoint == "get_forwarded_messages"
        return {
            "status": "ok",
            "retcode": 0,
            "data": {"messages": [{
                "user_id": 12345,
                "sender_name": "tester",
                "segments": [{"type": "text", "data": {"text": "hello"}}],
            }]},
        }

    actions, _ = _milky_actions(monkeypatch, responder)

    resolved = asyncio.run(actions.forward_solve(common.Message(segments.Forward("forward-1"))))

    assert isinstance(resolved, common.Message)
    assert isinstance(resolved[0], segments.Node)
    assert resolved[0].nickname == "tester"

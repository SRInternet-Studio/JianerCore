import asyncio
import json
from types import SimpleNamespace

from jianer import common, segments
from jianer.LecAdapters import Feishu
from jianer.LecAdapters.Feishu import Actions, FeishuEventServer, FeishuLongConnectionWorker
from jianer.LecAdapters.FeishuLib.Manager import Packet, reports
from jianer.LecAdapters.FeishuLib.client import FeishuClient
from jianer.LecAdapters.FeishuLib.translator import (
    build_hyper_event,
    feishu_message_to_segments,
    stringify_feishu_message,
)


class _Config:
    log_level = "INFO"

    def __init__(self, connection=None, others=None):
        self.connection = connection or SimpleNamespace()
        self.others = others or {}

    def get_connection(self, protocol=None):
        return self.connection


def _client(**connection_kwargs):
    defaults = {
        "app_id": "cli_app",
        "app_secret": "secret",
        "base_url": "https://open.feishu.cn",
        "verification_token": "verify",
        "encrypt_key": "encrypt",
        "callback_path": "/feishu/callback",
        "bot_open_id": "ou_bot",
        "event_mode": "webhook",
        "token_refresh_skew_seconds": 300,
    }
    defaults.update(connection_kwargs)
    connection = SimpleNamespace(**defaults)
    return FeishuClient(_Config(connection=connection))


def test_feishu_message_to_segments_parses_mentions():
    message = {
        "message_type": "text",
        "content": json.dumps({"text": 'hi <at user_id="ou_user">@Tom</at>'}),
    }

    assert feishu_message_to_segments(message) == [
        {"type": "text", "data": {"text": "hi "}},
        {"type": "at", "data": {"qq": "ou_user"}},
    ]


def test_feishu_message_event_translates_to_group_message():
    payload = {
        "header": {
            "event_type": "im.message.receive_v1",
            "create_time": "1608725989000",
        },
        "event": {
            "sender": {
                "sender_id": {"open_id": "ou_sender"},
                "name": "Tom",
            },
            "message": {
                "message_id": "om_msg",
                "chat_id": "oc_chat",
                "chat_type": "group",
                "message_type": "text",
                "content": json.dumps({"text": "hello"}),
            },
        },
    }

    event = build_hyper_event(payload, "ou_bot")

    assert event["post_type"] == "message"
    assert event["message_type"] == "group"
    assert event["group_id"] == "oc_chat"
    assert event["user_id"] == "ou_sender"
    assert event["self_id"] == "ou_bot"
    assert event["message"] == [{"type": "text", "data": {"text": "hello"}}]


def test_feishu_menu_event_translates_to_private_menu_message():
    payload = {
        "header": {
            "event_id": "evt_menu",
            "event_type": "application.bot.menu_v6",
            "create_time": "1608725989000",
        },
        "event": {
            "operator": {
                "operator_id": {"open_id": "ou_operator"},
                "name": "Tom",
            },
            "event_key": "menu_key",
        },
    }

    event = build_hyper_event(payload, "ou_bot")

    assert event["post_type"] == "message"
    assert event["message_type"] == "private"
    assert event["sub_type"] == "menu"
    assert event["user_id"] == "ou_operator"
    assert event["message"] == [{"type": "text", "data": {"text": "menu_key"}}]


def test_feishu_client_reads_connection_and_encodes_send(monkeypatch):
    client = _client(event_mode="long_connection", callback_path="/callback")
    calls = []

    def fake_request(method, path, params=None, json_body=None, data=None, files=None, auth=True):
        calls.append((method, path, params, json_body, data, files, auth))
        if path.endswith("/tenant_access_token/internal"):
            return {"tenant_access_token": "tenant-token", "expire": 7200}
        return {"data": {"message_id": "om_msg"}}

    monkeypatch.setattr(client, "_request", fake_request)

    assert client.event_mode == "long_connection"
    assert client.callback_path == "/callback"
    assert client.get_tenant_access_token() == "tenant-token"
    assert client.send_message("chat_id", "oc_chat", "text", {"text": "hello"}) == {"message_id": "om_msg"}

    assert calls[-1] == (
        "POST",
        "/open-apis/im/v1/messages",
        {"receive_id_type": "chat_id"},
        {
            "receive_id": "oc_chat",
            "msg_type": "text",
            "content": json.dumps({"text": "hello"}, ensure_ascii=False),
        },
        None,
        None,
        True,
    )


def test_feishu_packet_uses_client_call_and_stores_response(monkeypatch):
    client = _client()

    def fake_send_message(receive_id_type, receive_id, msg_type, content):
        return {
            "receive_id_type": receive_id_type,
            "receive_id": receive_id,
            "msg_type": msg_type,
            "content": content,
            "message_id": "om_msg",
        }

    monkeypatch.setattr(client, "send_message", fake_send_message)
    packet = Packet(
        "send_message",
        receive_id="oc_chat",
        receive_id_type="chat_id",
        msg_type="text",
        content={"text": "hello"},
    )

    response = packet.send_to(client)
    fetched = reports.get(packet.echo)

    assert response["status"] == "ok"
    assert response["echo"] == packet.echo
    assert fetched["data"]["message_id"] == "om_msg"


def test_feishu_actions_send_uploads_media_and_returns_last_message_id(monkeypatch):
    client = _client()
    calls = []

    def fake_upload_image(source):
        calls.append(("upload_image", source))
        return "img_uploaded"

    def fake_send_message(receive_id_type, receive_id, msg_type, content):
        calls.append(("send_message", receive_id_type, receive_id, msg_type, content))
        return {"message_id": f"{msg_type}_id"}

    monkeypatch.setattr(client, "upload_image", fake_upload_image)
    monkeypatch.setattr(client, "send_message", fake_send_message)

    message = common.Message(segments.Image("local.png"), segments.Text("hello"))
    result = asyncio.run(Actions(client).send(message, group_id="oc_chat"))

    assert result.status == "ok"
    assert result.data.message_id == "text_id"
    assert calls == [
        ("upload_image", "local.png"),
        ("send_message", "chat_id", "oc_chat", "image", {"image_key": "img_uploaded"}),
        ("send_message", "chat_id", "oc_chat", "text", {"text": "hello"}),
    ]


def test_feishu_actions_degrades_failed_image_upload_to_text(monkeypatch):
    client = _client()
    sent = []

    monkeypatch.setattr(client, "upload_image", lambda source: (_ for _ in ()).throw(RuntimeError("no permission")))
    monkeypatch.setattr(
        client,
        "send_message",
        lambda receive_id_type, receive_id, msg_type, content: sent.append(content) or {"message_id": "text_id"},
    )

    result = asyncio.run(Actions(client).send(common.Message(segments.Image("local.png")), group_id="oc_chat"))

    assert result.data.message_id == "text_id"
    assert sent[0]["text"]
    assert "im:resource" in sent[0]["text"]


def test_feishu_event_server_accepts_challenge_and_queues_events():
    client = _client()
    server = FeishuEventServer(client)
    test_client = server.app.test_client()

    challenge = test_client.post(
        "/feishu/callback",
        json={"type": "url_verification", "token": "verify", "challenge": "ok"},
    )

    assert challenge.status_code == 200
    assert challenge.get_json() == {"challenge": "ok"}

    payload = {
        "header": {"event_id": "evt_1", "token": "verify"},
        "event": {"message": {"message_id": "om_msg"}},
    }
    response = test_client.post("/feishu/callback", json=payload)

    assert response.status_code == 200
    assert server.queue.get_nowait() == payload


def test_feishu_long_connection_worker_pushes_registered_events(monkeypatch):
    client = _client(app_id="cli_app", app_secret="secret")
    event_queue = Feishu.queue.Queue()
    created = {}

    class FakeLark:
        class JSON:
            @staticmethod
            def marshal(data):
                return json.dumps(data)

        class LogLevel:
            INFO = "INFO"

        class ws:
            class Client:
                def __init__(self, app_id, app_secret, log_level=None, event_handler=None):
                    created["ws"] = self
                    self.app_id = app_id
                    self.app_secret = app_secret
                    self.log_level = log_level
                    self.event_handler = event_handler
                    self.started = False

                def start(self):
                    self.started = True

    class FakeBuilder:
        def __init__(self, encrypt_key, verification_token):
            created["builder"] = self
            self.encrypt_key = encrypt_key
            self.verification_token = verification_token
            self.handlers = {}

        def register_p2_im_message_receive_v1(self, handler):
            self.handlers["message"] = handler
            return self

        def register_p2_application_bot_menu_v6(self, handler):
            self.handlers["menu"] = handler
            return self

        def register_p2_im_chat_access_event_bot_p2p_chat_entered_v1(self, handler):
            self.handlers["p2p"] = handler
            return self

        def build(self):
            return self

    fake_dispatcher = SimpleNamespace(EventDispatcherHandlerBuilder=FakeBuilder)

    def fake_import(name):
        if name == "lark_oapi":
            return FakeLark
        if name == "lark_oapi.event.dispatcher_handler":
            return fake_dispatcher
        raise AssertionError(name)

    monkeypatch.setattr(Feishu.importlib, "import_module", fake_import)

    worker = FeishuLongConnectionWorker(client, event_queue)
    worker.run()

    assert created["ws"].started is True
    assert created["ws"].event_handler is created["builder"]
    assert created["builder"].verification_token == "verify"
    assert created["builder"].encrypt_key == "encrypt"
    assert set(created["builder"].handlers) == {"message", "menu", "p2p"}

    payload = {"header": {"event_type": "im.message.receive_v1"}}
    created["builder"].handlers["message"](payload)

    assert event_queue.get_nowait() == payload


def test_feishu_stringify_message_prefers_text():
    assert stringify_feishu_message({"content": json.dumps({"text": "hello"})}) == "hello"

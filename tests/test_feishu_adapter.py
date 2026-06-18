import json
import asyncio
from types import SimpleNamespace

from jianer.LecAdapters.Feishu import Actions, _message_to_feishu
from jianer.LecAdapters.FeishuLib.Manager import Packet, reports
from jianer.LecAdapters.FeishuLib.client import (
    FeishuHttpConnection,
    FeishuOapiConnection,
    feishu_content_to_onebot,
    translate_event,
)
from jianer import common, segments


class FakeValueBuilder:
    def __init__(self, value=None):
        self.value = value or SimpleNamespace()

    def __getattr__(self, item):
        def setter(value):
            setattr(self.value, item, value)
            return self

        return setter

    def build(self):
        return self.value


class FakeRequestOption:
    @staticmethod
    def builder():
        return FakeValueBuilder(SimpleNamespace())


class FakeBaseRequest:
    @staticmethod
    def builder():
        return FakeValueBuilder(SimpleNamespace())


class FakeCreateMessageRequestBody:
    @staticmethod
    def builder():
        return FakeValueBuilder(SimpleNamespace())


class FakeCreateMessageRequest:
    @staticmethod
    def builder():
        return FakeValueBuilder(SimpleNamespace())


class FakeMessageApi:
    def __init__(self):
        self.calls = []

    def create(self, request, option=None):
        self.calls.append((request, option))
        return SimpleNamespace(
            code=0,
            msg="success",
            data=SimpleNamespace(message_id="om_msg"),
        )


class FakeApiClient:
    def __init__(self):
        self.generic_requests = []
        self.im = SimpleNamespace(v1=SimpleNamespace(message=FakeMessageApi()))

    def request(self, request, option=None):
        self.generic_requests.append((request, option))
        return SimpleNamespace(
            code=0,
            msg="success",
            raw=SimpleNamespace(
                content=b'{"code":0,"msg":"success","bot":{"open_id":"ou_bot","app_name":"Bot"}}',
            ),
        )


class FakeClientBuilder:
    def __init__(self, owner):
        self.owner = owner
        self.config = SimpleNamespace()

    def __getattr__(self, item):
        def setter(value):
            setattr(self.config, item, value)
            return self

        return setter

    def build(self):
        client = FakeApiClient()
        self.owner.last_client = client
        self.owner.last_config = self.config
        return client


class FakeClientFactory:
    last_client = None
    last_config = None

    @classmethod
    def builder(cls):
        return FakeClientBuilder(cls)


class FakeWsClient:
    instances = []

    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        self.started = False
        self.instances.append(self)

    def start(self):
        self.started = True


class FakeEventBuilder:
    def __init__(self, encrypt_key, verification_token):
        self.encrypt_key = encrypt_key
        self.verification_token = verification_token
        self.handlers = {}

    def register_p2_im_message_receive_v1(self, handler):
        self.handlers["message"] = handler
        return self

    def register_p2_application_bot_menu_v6(self, handler):
        self.handlers["menu"] = handler
        return self

    def build(self):
        return self


class FakeEventDispatcherHandler:
    last_builder = None

    @classmethod
    def builder(cls, encrypt_key, verification_token):
        cls.last_builder = FakeEventBuilder(encrypt_key, verification_token)
        return cls.last_builder


class FakeLark:
    Client = FakeClientFactory
    EventDispatcherHandler = FakeEventDispatcherHandler
    RequestOption = FakeRequestOption
    BaseRequest = FakeBaseRequest
    LogLevel = SimpleNamespace(DEBUG="DEBUG", INFO="INFO", WARNING="WARNING", ERROR="ERROR")
    HttpMethod = SimpleNamespace(GET="GET", POST="POST", PUT="PUT", PATCH="PATCH", DELETE="DELETE")
    AccessTokenType = SimpleNamespace(TENANT="TENANT")
    ws = SimpleNamespace(Client=FakeWsClient)
    im = SimpleNamespace(
        v1=SimpleNamespace(
            CreateMessageRequestBody=FakeCreateMessageRequestBody,
            CreateMessageRequest=FakeCreateMessageRequest,
        )
    )


def fake_lark():
    FakeWsClient.instances = []
    FakeClientFactory.last_client = None
    FakeClientFactory.last_config = None
    FakeEventDispatcherHandler.last_builder = None
    return FakeLark


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


def test_feishu_oapi_connection_starts_long_connection_and_translates_events():
    lark = fake_lark()
    connection = FeishuOapiConnection(
        "cli_app",
        "secret",
        verification_token="verify",
        encrypt_key="encrypt",
        lark_module=lark,
    )

    connection.connect()

    assert connection.listener_started is True
    assert FakeWsClient.instances[-1].started is True
    assert FakeWsClient.instances[-1].kwargs["event_handler"] is FakeEventDispatcherHandler.last_builder
    assert FakeEventDispatcherHandler.last_builder.verification_token == "verify"
    assert FakeEventDispatcherHandler.last_builder.encrypt_key == "encrypt"
    assert set(FakeEventDispatcherHandler.last_builder.handlers) == {"message", "menu"}

    event_payload = SimpleNamespace(
        header=SimpleNamespace(
            event_id="evt_1",
            event_type="im.message.receive_v1",
            create_time="1608725989000",
            app_id="cli_app",
        ),
        event=SimpleNamespace(
            sender=SimpleNamespace(sender_id=SimpleNamespace(open_id="ou_sender")),
            message=SimpleNamespace(
                message_id="om_msg",
                create_time="1609073151345",
                chat_id="oc_chat",
                chat_type="group",
                message_type="text",
                content='{"text":"hello"}',
            ),
        ),
    )
    FakeEventDispatcherHandler.last_builder.handlers["message"](event_payload)

    event = connection.recv()

    assert event["message_type"] == "group"
    assert event["group_id"] == "oc_chat"
    assert event["user_id"] == "ou_sender"
    assert event["message"] == [{"type": "text", "data": {"text": "hello"}}]


def test_feishu_oapi_connection_sends_message_with_lark_sdk():
    lark = fake_lark()
    connection = FeishuOapiConnection("cli_app", "secret", tenant_access_token="t-token", lark_module=lark)

    response = connection.send_message(
        receive_id="oc_chat",
        receive_id_type="chat_id",
        msg_type="text",
        content={"text": "hello"},
        uuid="uuid-1",
    )

    client = FakeClientFactory.last_client
    request, option = client.im.v1.message.calls[0]

    assert response["data"]["message_id"] == "om_msg"
    assert request.receive_id_type == "chat_id"
    assert request.request_body.receive_id == "oc_chat"
    assert request.request_body.msg_type == "text"
    assert request.request_body.content == json.dumps({"text": "hello"}, ensure_ascii=False)
    assert request.request_body.uuid == "uuid-1"
    assert option.tenant_access_token == "t-token"


def test_feishu_oapi_connection_gets_bot_info_with_lark_request():
    lark = fake_lark()
    connection = FeishuOapiConnection("cli_app", "secret", lark_module=lark)

    response = connection.get_bot_info()

    client = FakeClientFactory.last_client
    request, option = client.generic_requests[0]

    assert response["bot"]["open_id"] == "ou_bot"
    assert connection.bot_open_id == "ou_bot"
    assert request.http_method == "GET"
    assert request.uri == "/open-apis/bot/v3/info"
    assert request.token_types == {"TENANT"}
    assert option is None

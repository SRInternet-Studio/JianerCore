import json
import logging
import queue
import threading
import time
from typing import Optional

import flask
import httpx

from ...adapters.obuilder import OneBotEventBuilder, OneBotJsonMessageBuilder


FEISHU_BASE_URL = "https://open.feishu.cn"


def parse_json_content(content) -> dict:
    if isinstance(content, dict):
        return content
    if not content:
        return {}
    if isinstance(content, str):
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            return {"text": content}
        return parsed if isinstance(parsed, dict) else {"value": parsed}
    return {"value": content}


def _timestamp_seconds(value) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return int(time.time())
    if number > 9999999999:
        return number // 1000
    return number


def _user_id(user: dict, preferred: str = "open_id") -> Optional[str]:
    if not isinstance(user, dict):
        return None
    return user.get(preferred) or user.get("open_id") or user.get("user_id") or user.get("union_id")


def _sender_id(sender: dict, preferred: str = "open_id") -> Optional[str]:
    if not isinstance(sender, dict):
        return None
    return _user_id(sender.get("sender_id") or sender.get("operator_id") or sender, preferred)


def feishu_content_to_onebot(message_type: str, content, mentions: list = None) -> list[dict]:
    data = parse_json_content(content)
    builder = OneBotJsonMessageBuilder()
    mentions = mentions or []

    if message_type == "text":
        text = str(data.get("text", ""))
        if len(mentions) == 0:
            return builder.text(text).build()

        cursor = 0
        for mention in sorted(mentions, key=lambda item: text.find(item.get("key", ""))):
            key = mention.get("key")
            if not key:
                continue
            index = text.find(key, cursor)
            if index < 0:
                continue
            if index > cursor:
                builder.text(text[cursor:index])
            mention_id = mention.get("id") or {}
            if mention.get("mentioned_type") == "bot" and not _user_id(mention_id):
                builder.at("all")
            else:
                builder.at(str(_user_id(mention_id) or key))
            cursor = index + len(key)
        if cursor < len(text):
            builder.text(text[cursor:])
        return builder.build()

    if message_type == "image":
        image_key = data.get("image_key") or data.get("file_key") or data.get("url")
        return builder.image(image_key or "", summary="[Image]").build()

    if message_type == "audio":
        file_key = data.get("file_key") or data.get("audio_key") or data.get("url")
        return builder.record(file_key or "").build()

    if message_type == "media":
        file_key = data.get("file_key") or data.get("video_key") or data.get("url")
        return builder.video(file_key or "").build()

    return builder.json(data).build()


def translate_message_event(payload: dict, preferred_user_id: str = "open_id", self_id: str = None) -> Optional[dict]:
    event = payload.get("event") or {}
    header = payload.get("header") or {}
    sender = event.get("sender") or {}
    message = event.get("message") or {}

    sender_id = _sender_id(sender, preferred_user_id)
    chat_id = message.get("chat_id")
    if sender_id is None or chat_id is None:
        return None

    chat_type = message.get("chat_type")
    message_type = message.get("message_type")
    translated_message = feishu_content_to_onebot(
        message_type,
        message.get("content"),
        message.get("mentions") or [],
    )
    event_time = _timestamp_seconds(message.get("create_time") or header.get("create_time"))
    message_id = message.get("message_id") or header.get("event_id") or ""
    bot_id = self_id or header.get("app_id") or ""

    builder = OneBotEventBuilder()
    if chat_type == "group":
        return builder \
            .init(event_time, bot_id, sender_id, chat_id) \
            .as_group_message(translated_message, str(message_id)) \
            .group_sender(str(sender_id), "unknown", 0, "", "", "", "member", "") \
            .build()

    return builder \
        .init(event_time, bot_id, sender_id, 0) \
        .as_private_message(translated_message, str(message_id)) \
        .private_sender(str(sender_id), "unknown", 0) \
        .build()


def translate_menu_event(payload: dict, preferred_user_id: str = "open_id", self_id: str = None) -> Optional[dict]:
    event = payload.get("event") or {}
    header = payload.get("header") or {}
    operator = event.get("operator") or {}
    operator_id = _user_id(operator.get("operator_id") or {}, preferred_user_id)
    if operator_id is None:
        return None

    return {
        "time": _timestamp_seconds(event.get("timestamp") or header.get("create_time")),
        "self_id": self_id or header.get("app_id") or "",
        "post_type": "notice",
        "notice_type": "bot_menu",
        "user_id": operator_id,
        "group_id": 0,
        "operator_id": operator_id,
        "operator_name": operator.get("operator_name", ""),
        "event_key": event.get("event_key", ""),
        "feishu_event": payload,
    }


def translate_event(payload: dict, preferred_user_id: str = "open_id", self_id: str = None) -> Optional[dict]:
    event_type = (payload.get("header") or {}).get("event_type") or payload.get("type")
    if event_type == "im.message.receive_v1":
        return translate_message_event(payload, preferred_user_id, self_id)
    if event_type == "application.bot.menu_v6":
        return translate_menu_event(payload, preferred_user_id, self_id)
    return None


class FeishuHttpConnection:
    def __init__(
            self,
            app_id: str,
            app_secret: str,
            host: str = "0.0.0.0",
            port: int = 8080,
            endpoint: str = "/",
            verification_token: str = None,
            base_url: str = FEISHU_BASE_URL,
            user_id_type: str = "open_id",
            tenant_access_token: str = None,
    ):
        self.app_id = app_id
        self.app_secret = app_secret
        self.host = host
        self.port = int(port)
        self.endpoint = endpoint if endpoint.startswith("/") else f"/{endpoint}"
        self.verification_token = verification_token
        self.base_url = base_url.rstrip("/")
        self.user_id_type = user_id_type
        self.tenant_access_token = tenant_access_token
        self.token_expires_at = 0.0
        self.bot_open_id = None
        self.reports = queue.Queue()
        self.listener_started = False
        self.app = flask.Flask(f"{__name__}.{id(self)}")
        self.app.config["LOGGER_HANDLER_POLICY"] = "never"
        logging.getLogger("werkzeug").setLevel(logging.ERROR)
        self._register_routes()

    def _register_routes(self) -> None:
        @self.app.route(self.endpoint, methods=["POST"])
        def listener():
            payload = flask.request.get_json(silent=True) or {}
            if payload.get("encrypt"):
                return flask.jsonify({"msg": "encrypted Feishu events are not supported"}), 400
            if payload.get("type") == "url_verification":
                if self._token_matches(payload.get("token")):
                    return flask.jsonify({"challenge": payload.get("challenge")})
                return flask.jsonify({"msg": "invalid verification token"}), 403
            if not self._token_matches((payload.get("header") or {}).get("token") or payload.get("token")):
                return flask.jsonify({"msg": "invalid verification token"}), 403

            event = translate_event(payload, self.user_id_type, self.bot_open_id or self.app_id)
            if event is not None:
                self.reports.put(event)
            return flask.jsonify({})

    def _token_matches(self, token: str) -> bool:
        return not self.verification_token or token == self.verification_token

    def connect(self) -> None:
        if self.listener_started:
            return
        thread = threading.Thread(
            target=lambda: self.app.run(host=self.host, port=self.port),
            daemon=True,
        )
        thread.start()
        self.listener_started = True

    def close(self) -> None:
        self.listener_started = False

    def recv(self) -> dict:
        return self.reports.get()

    def get_tenant_access_token(self, force: bool = False) -> str:
        if (
                not force
                and self.tenant_access_token
                and (self.token_expires_at == 0 or time.time() < self.token_expires_at)
        ):
            return self.tenant_access_token

        response = httpx.post(
            f"{self.base_url}/open-apis/auth/v3/tenant_access_token/internal",
            json={"app_id": self.app_id, "app_secret": self.app_secret},
            headers={"Content-Type": "application/json; charset=utf-8"},
            timeout=15.0,
        )
        data = response.json()
        if data.get("code") != 0:
            raise RuntimeError(f"Feishu tenant_access_token failed: {data}")
        self.tenant_access_token = data["tenant_access_token"]
        expire = int(data.get("expire") or 7200)
        self.token_expires_at = time.time() + max(expire - 60, 0)
        return self.tenant_access_token

    def request(self, method: str, path: str, *, params: dict = None, json_body: dict = None, auth: bool = True) -> dict:
        headers = {"Content-Type": "application/json; charset=utf-8"}
        if auth:
            headers["Authorization"] = f"Bearer {self.get_tenant_access_token()}"
        response = httpx.request(
            method,
            f"{self.base_url}{path}",
            params=params,
            json=json_body,
            headers=headers,
            timeout=15.0,
        )
        try:
            return response.json()
        except json.JSONDecodeError:
            return {"code": response.status_code, "msg": response.text, "data": {}}

    def send_message(
            self,
            receive_id: str,
            msg_type: str,
            content,
            receive_id_type: str = "open_id",
            uuid: str = None,
    ) -> dict:
        body = {
            "receive_id": str(receive_id),
            "msg_type": msg_type,
            "content": content if isinstance(content, str) else json.dumps(content, ensure_ascii=False),
        }
        if uuid:
            body["uuid"] = uuid
        return self.request(
            "POST",
            "/open-apis/im/v1/messages",
            params={"receive_id_type": receive_id_type},
            json_body=body,
        )

    def get_bot_info(self) -> dict:
        response = self.request("GET", "/open-apis/bot/v3/info")
        bot = response.get("bot") or {}
        self.bot_open_id = bot.get("open_id") or self.bot_open_id
        return response

    def call(self, endpoint: str, **kwargs) -> dict:
        if endpoint == "send_message":
            return self.send_message(**kwargs)
        if endpoint == "get_bot_info":
            return self.get_bot_info()
        method = kwargs.pop("method", "POST")
        path = kwargs.pop("path", endpoint)
        return self.request(method, path, params=kwargs.pop("params", None), json_body=kwargs or None)


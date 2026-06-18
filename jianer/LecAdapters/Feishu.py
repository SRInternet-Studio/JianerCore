import asyncio
import sys
import threading
import time

from .. import common, configurator, events, hyperogger, segments
from ..events import *
from ..utils import errors
from ..utils.apiresponse import *
from ..utils.hypetyping import Any, NoReturn, Union
from .FeishuLib.Manager import Packet, reports
from .FeishuLib.client import FeishuHttpConnection, FeishuOapiConnection, parse_json_content

config = configurator.BotConfig.get("jianer-bot")
logger = hyperogger.Logger()
logger.set_level(config.log_level if config else "INFO")
listener_ran = False


def _config_value(source: Any, name: str, default=None):
    if isinstance(source, dict):
        return source.get(name, default)
    return getattr(source, name, default)


def _message_to_feishu(message: Union[common.Message, str]) -> tuple[str, dict]:
    if isinstance(message, str):
        return "text", {"text": message}
    text_parts = []
    for seg in message:
        if isinstance(seg, segments.Text):
            text_parts.append(seg.text)
        elif isinstance(seg, segments.At):
            if seg.qq == "all":
                text_parts.append("<at id=\"all\"></at>")
            else:
                text_parts.append(f"<at id=\"{seg.qq}\"></at>")
        elif isinstance(seg, segments.Json):
            data = parse_json_content(seg.data)
            return "interactive" if data.get("elements") or data.get("type") == "template" else "post", data
        else:
            text_parts.append(str(seg))
    return "text", {"text": "".join(text_parts)}


class Actions:
    def __init__(self, cnt: Union[FeishuHttpConnection, FeishuOapiConnection]):
        self.connection = cnt

        class CustomAction:
            def __init__(self, cnt_i: Union[FeishuHttpConnection, FeishuOapiConnection]):
                self.connection = cnt_i

            def __getattr__(self, item) -> callable:
                async def wrapper(**kwargs) -> str:
                    packet = Packet(str(item), **kwargs)
                    packet.send_to(self.connection)
                    return packet.echo

                return wrapper

        self.custom = CustomAction(self.connection)

    @staticmethod
    def _is_successful_response(res: Any) -> bool:
        return isinstance(res, dict) and res.get("retcode") == 0

    async def send(
            self, message: Union[common.Message, str], group_id: str = None, user_id: str = None
    ) -> common.Ret[MsgSendRsp]:
        msg_type, content = _message_to_feishu(message)
        if group_id is not None:
            receive_id = str(group_id)
            receive_id_type = "chat_id"
        elif user_id is not None:
            receive_id = str(user_id)
            receive_id_type = self.connection.user_id_type
        else:
            raise errors.ArgsInvalidError("'send' API requires 'group_id' or 'user_id' but none of them are provided.")

        packet = Packet(
            "send_message",
            receive_id=receive_id,
            receive_id_type=receive_id_type,
            msg_type=msg_type,
            content=content,
        )
        res = packet.send_to(self.connection)
        if not self._is_successful_response(res):
            logger.error(f"Feishu send failed: {res}")
            raise errors.ActionFailedError(f"Feishu send failed: {res}")

        logger.info(f"Sent Feishu message to {receive_id_type} {receive_id}: {message}")
        return common.Ret(reports.get(packet.echo), MsgSendRsp)

    async def get_login_info(self) -> common.Ret[GetLoginInfoRsp]:
        packet = Packet("get_bot_info")
        res = packet.send_to(self.connection)
        if isinstance(res.get("data"), dict):
            bot = res["data"]
            bot["user_id"] = bot.get("open_id") or bot.get("app_id") or self.connection.app_id
            bot["nickname"] = bot.get("app_name") or bot.get("name") or "Feishu Bot"
        return common.Ret(reports.get(packet.echo), GetLoginInfoRsp)

    async def get_version_info(self) -> common.Ret[GetVerInfoRsp]:
        echo = "feishu_version_info"
        reports.put(echo, {
            "status": "ok",
            "retcode": 0,
            "data": {
                "app_name": "Feishu",
                "app_version": "bot-v3",
                "protocol_version": "im-v1",
            },
            "echo": echo,
        })
        return common.Ret(reports.get(echo), GetVerInfoRsp)

    async def get_status(self) -> common.Ret:
        echo = "feishu_status"
        reports.put(echo, {
            "status": "ok",
            "retcode": 0,
            "data": {"online": True, "listener_started": self.connection.listener_started},
            "echo": echo,
        })
        return common.Ret(reports.get(echo))

    async def del_message(self, message_id: int) -> None:
        raise NotImplementedError("Feishu message deletion is not implemented yet.")

    async def set_group_kick(self, group_id: int, user_id: int) -> None:
        raise NotImplementedError("Feishu group member kick is not implemented yet.")

    async def set_group_ban(self, group_id: int, user_id: int, duration: int = 60) -> None:
        raise NotImplementedError("Feishu group mute is not implemented yet.")

    async def send_forward_msg(self, message: common.Message) -> common.Ret[SendForwardRsp]:
        raise NotImplementedError("Feishu forward messages are not implemented yet.")

    async def get_forward_msg(self, sid: str) -> common.Ret[common.Message]:
        raise NotImplementedError("Feishu forward messages are not implemented yet.")

    async def forward_solve(self, message: common.Message) -> common.Message:
        raise NotImplementedError("Feishu forward messages are not implemented yet.")

    async def send_group_forward_msg(self, group_id: int, message: common.Message) -> common.Ret[SendGrpForwardRsp]:
        raise NotImplementedError("Feishu forward messages are not implemented yet.")

    async def set_group_add_request(self, flag: str, sub_type: str, approve: bool,
                                    reason: str = "Not Mentioned") -> None:
        raise NotImplementedError("Feishu group add requests are not implemented yet.")

    async def get_stranger_info(self, user_id: int) -> common.Ret[GetStrInfoRsp]:
        raise NotImplementedError("Feishu user profile lookup is not implemented yet.")

    async def get_group_member_info(self, group_id: int, user_id: int) -> common.Ret[GetGrpMemInfoRsp]:
        raise NotImplementedError("Feishu group member lookup is not implemented yet.")

    async def get_group_info(self, group_id: int) -> common.Ret[GetGrpInfoRsp]:
        raise NotImplementedError("Feishu group info lookup is not implemented yet.")

    async def set_essence_msg(self, message_id: int) -> None:
        raise NotImplementedError("Feishu essence messages are not implemented yet.")

    async def set_group_special_title(self, group_id: int, user_id: int, title: str) -> None:
        raise NotImplementedError("Feishu group titles are not implemented yet.")

    async def get_msg(self, msg_id: int) -> common.Ret[GetMsgRsp]:
        raise NotImplementedError("Feishu get message is not implemented yet.")

    async def send_callback(self, group_id: int, bot_id: int, data: dict) -> None:
        raise NotImplementedError("Feishu callbacks are not implemented yet.")


async def tester(message_data: Union[events.Event, events.HyperNotify], actions: Actions) -> None:
    ...


def __handler(data: Union[dict, events.HyperNotify], actions: Actions) -> None:
    if isinstance(data, dict):
        asyncio.run(handler(events.em.new(data), actions))
    else:
        asyncio.run(handler(data, actions))


handler: callable = tester


def reg(func: callable) -> None:
    global handler
    handler = func


connection: Union[FeishuHttpConnection, FeishuOapiConnection]


def _build_connection() -> Union[FeishuHttpConnection, FeishuOapiConnection]:
    conn_config = config.connection
    others = config.others if isinstance(config.others, dict) else {}
    app_id = _config_value(conn_config, "app_id") or others.get("feishu_app_id")
    app_secret = _config_value(conn_config, "app_secret") or others.get("feishu_app_secret")
    if not app_id or not app_secret:
        raise errors.ArgsInvalidError("Feishu adapter requires app_id and app_secret in connection config.")

    mode = str(_config_value(conn_config, "mode", "OAPI") or "OAPI").lower()
    verification_token = _config_value(conn_config, "verification_token") or others.get("feishu_verification_token")
    base_url = _config_value(conn_config, "base_url", "https://open.feishu.cn")
    user_id_type = _config_value(conn_config, "user_id_type", "open_id")
    tenant_access_token = _config_value(conn_config, "tenant_access_token")
    if mode in {"webhook", "http", "httpc", "callback"}:
        return FeishuHttpConnection(
            app_id=app_id,
            app_secret=app_secret,
            host=_config_value(conn_config, "host", "0.0.0.0"),
            port=_config_value(conn_config, "port", 8080),
            endpoint=_config_value(conn_config, "endpoint", "/"),
            verification_token=verification_token,
            base_url=base_url,
            user_id_type=user_id_type,
            tenant_access_token=tenant_access_token,
        )

    return FeishuOapiConnection(
        app_id=app_id,
        app_secret=app_secret,
        verification_token=verification_token,
        encrypt_key=_config_value(conn_config, "encrypt_key") or others.get("feishu_encrypt_key"),
        base_url=base_url,
        user_id_type=user_id_type,
        tenant_access_token=tenant_access_token,
        log_level=_config_value(conn_config, "log_level", config.log_level if config else "INFO"),
    )


def run() -> NoReturn:
    global connection, listener_ran
    listener_ran = True
    try:
        if handler is tester:
            raise errors.ListenerNotRegisteredError("No handler registered")

        connection = _build_connection()
        connection.connect()
        actions = Actions(connection)
        data = HyperListenerStartNotify(
            time_now=int(time.time()),
            notify_type="listener_start",
            connection=connection,
        )
        threading.Thread(target=lambda: __handler(data, actions), daemon=True).start()
        if isinstance(connection, FeishuHttpConnection):
            logger.info(f"Feishu event listener started at http://{connection.host}:{connection.port}{connection.endpoint}")
        else:
            logger.info(f"Feishu Lark OAPI long connection started for app_id {connection.app_id}")

        while True:
            data = connection.recv()
            threading.Thread(target=lambda: __handler(data, actions), daemon=True).start()
    except KeyboardInterrupt:
        logger.warning("Exiting after Ctrl+C")
        try:
            connection.close()
        except Exception:
            pass
        sys.exit()


def stop() -> None:
    try:
        connection.close()
    except Exception:
        pass

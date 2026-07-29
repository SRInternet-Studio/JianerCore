from ..utils.hypetyping import Any, NoReturn, TypeVar, Callable, Optional
from ..utils.apiresponse import *
from ..events import *
from .. import common, configurator, events, hyperogger, segments
from ..asyncio_runner import run_awaitable
from ..utils import errors
from ..utils.typextensions import ObjectedJson

from .MilkyLib.translator import MilkyHttpConnection, MilkyOutGoingSegBuilder, msg_deid, msg_enid, message_translator
from .MilkyLib.Manager import Packet, reports
from .MilkyLib.types import MilkyOutgoingSegment, consume_segment, consume_segments, make_text_segment
from ..adapters.contracts import (
    Capability,
    ConversationKey,
    ConversationKind,
    DEFAULT_MEDIA_POLICY,
    ExternalId,
    MediaPolicy,
    MediaRequest,
    MediaResolution,
    ReferenceResolution,
    ResolutionErrorCode,
    ResolutionStatus,
)
from ..adapters.media import media_failure, resolve_media_request
from ..adapters.resolution import (
    numeric_external_id,
    positive_timeout_seconds,
    reference_failure,
    reference_success,
    response_segments,
)

import time
import threading
import asyncio
import json
import sys

config = configurator.BotConfig.get("jianer-bot")
logger = hyperogger.Logger()
logger.set_level(config.log_level if config else "INFO")
listener_ran = False
MILKY_TEXT_CHUNK_LIMIT = 1800
MILKY_TEXT_RECOVERY_CHUNK_LIMIT = 400


def _fetch_ret(echo: str, serializer=ObjectedJson) -> common.Ret:
    return common.Ret(reports.get(echo), serializer)


class Actions:
    protocol = "milky"
    capabilities = frozenset({
        Capability.RESOLVE_REFERENCE,
        Capability.RESOLVE_MEDIA,
        Capability.SEND_REPLY,
        Capability.SEND_IMAGE,
        Capability.SEND_AUDIO,
        Capability.NATIVE_GROUP_FORWARD,
        Capability.RESOLVE_FORWARD,
    })

    def __init__(self, cnt: MilkyHttpConnection):
        self.connection = cnt

        class CustomAction:
            def __init__(self, cnt_i: MilkyHttpConnection):
                self.connection = cnt_i

            def __getattr__(self, item) -> callable:
                async def wrapper(**kwargs) -> str:
                    packet = Packet(
                        str(item),
                        **kwargs
                    )
                    packet.send_to(self.connection)
                    return packet.echo

                return wrapper

        self.custom = CustomAction(self.connection)

    @staticmethod
    def _numeric_id(value, field_name: str) -> int:
        try:
            return numeric_external_id(value, field_name)
        except ValueError as exc:
            raise errors.ArgsInvalidError(str(exc)) from exc

    @staticmethod
    def _is_successful_response(res: Any) -> bool:
        if not isinstance(res, dict):
            return False
        return res.get("status") == "ok" and res.get("retcode") == 0

    @staticmethod
    def _split_text(text: str, limit: int = MILKY_TEXT_CHUNK_LIMIT) -> list[str]:
        if len(text) <= limit:
            return [text]

        chunks: list[str] = []
        current = ""
        for line in text.splitlines(keepends=True):
            if len(line) > limit:
                if current:
                    chunks.append(current)
                    current = ""
                chunks.extend(line[index:index + limit] for index in range(0, len(line), limit))
                continue
            if current and len(current) + len(line) > limit:
                chunks.append(current)
                current = line
            else:
                current += line
        if current:
            chunks.append(current)
        return chunks or [text]

    @staticmethod
    def _text_chunk_payloads(
            payload: dict, *, keep_reply: bool = True, limit: int = MILKY_TEXT_CHUNK_LIMIT
    ) -> Optional[list[dict]]:
        message = payload.get("message")
        if not isinstance(message, list):
            return None

        reply_segment = None
        text_parts: list[str] = []
        for segment in message:
            if not isinstance(segment, dict):
                return None
            segment_type = segment.get("type")
            data = segment.get("data")
            if segment_type == "reply":
                if reply_segment is not None:
                    return None
                reply_segment = segment
                continue
            if segment_type != "text" or not isinstance(data, dict):
                return None
            text_parts.append(str(data.get("text", "")))

        chunks = Actions._split_text("".join(text_parts), limit)
        if len(chunks) <= 1:
            return None

        payloads: list[dict] = []
        for index, chunk in enumerate(chunks):
            chunk_payload = payload.copy()
            chunk_message = []
            if index == 0 and keep_reply and reply_segment is not None:
                chunk_message.append(reply_segment)
            chunk_message.append(make_text_segment(chunk))
            chunk_payload["message"] = chunk_message
            payloads.append(chunk_payload)
        return payloads

    def _send_text_chunks(
            self, endpoint: str, payloads: list[dict], *, interval: float = 0
    ) -> tuple[Packet, dict]:
        packet: Packet = None
        res: dict = {}
        for index, payload in enumerate(payloads):
            if interval > 0 and index > 0:
                time.sleep(interval)
            packet = Packet(endpoint, **payload)
            res = packet.send_to(self.connection)
            if not self._is_successful_response(res):
                return packet, res if isinstance(res, dict) else {}
        return packet, res

    @staticmethod
    def _is_payload_rejection(res: Any) -> bool:
        if not isinstance(res, dict):
            return False
        return res.get("retcode") in (-500, -400, 400, 500)

    def _send_action(self, endpoint: str, **payload) -> tuple[Packet, dict]:
        packet = Packet(endpoint, **payload)
        res = packet.send_to(self.connection)
        if not self._is_successful_response(res):
            raise errors.ActionFailedError(f"Milky action {endpoint} failed: {res}")
        return packet, res

    @staticmethod
    def _segment_to_outgoing(seg: Any) -> MilkyOutgoingSegment:
        if hasattr(seg, "milky_outgoing_seg"):
            outgoing_seg = consume_segment(seg.milky_outgoing_seg())
            if outgoing_seg is not None:
                return outgoing_seg

        builder = MilkyOutGoingSegBuilder()
        if isinstance(seg, segments.Text):
            return builder.text(seg.text).build()[0]
        if isinstance(seg, segments.At):
            if str(seg.qq) == "all":
                return builder.mention_all().build()[0]
            return builder.mention(int(seg.qq)).build()[0]
        if isinstance(seg, segments.Reply):
            message_id = int(seg.id)
            seq = msg_deid(message_id)[1] if message_id >= (1 << 64) else message_id
            return builder.reply(seq).build()[0]
        if isinstance(seg, segments.Faces):
            return builder.face(str(seg.id)).build()[0]
        if isinstance(seg, segments.Image):
            return builder.image(seg.file, getattr(seg, "summary", "[Image]")).build()[0]
        if isinstance(seg, segments.Record):
            return builder.record(seg.file).build()[0]
        if isinstance(seg, segments.Video):
            return builder.video(seg.file).build()[0]
        if hasattr(seg, "text"):
            return make_text_segment(str(getattr(seg, "text")))
        raise ValueError(f"Unsupported Milky outgoing segment type: {type(seg).__name__}")

    @staticmethod
    def _json_segment_to_outgoing(segment: dict) -> MilkyOutgoingSegment:
        if not isinstance(segment, dict) or not isinstance(segment.get("data"), dict):
            raise ValueError("Forward node content must contain message segment objects.")
        segment_type = segment.get("type")
        data = segment["data"]
        builder = MilkyOutGoingSegBuilder()
        if segment_type == "text":
            return builder.text(str(data.get("text", ""))).build()[0]
        if segment_type == "at":
            qq = data.get("qq")
            return (builder.mention_all() if str(qq) == "all" else builder.mention(int(qq))).build()[0]
        if segment_type == "reply":
            message_id = int(data.get("id"))
            seq = msg_deid(message_id)[1] if message_id >= (1 << 64) else message_id
            return builder.reply(seq).build()[0]
        if segment_type == "face":
            return builder.face(str(data.get("id"))).build()[0]
        if segment_type == "image":
            return builder.image(data.get("file"), data.get("summary", "[Image]")).build()[0]
        if segment_type == "record":
            return builder.record(data.get("file")).build()[0]
        if segment_type == "video":
            return builder.video(data.get("file")).build()[0]
        raise ValueError(f"Unsupported Milky forward-node segment type: {segment_type}")

    async def send(
            self, message: Union[common.Message, str], group_id: ExternalId = None,
            user_id: ExternalId = None
    ) -> common.Ret[MsgSendRsp]:
        if isinstance(message, str):
            message = common.Message(segments.Text(message))
        outgoing: list[MilkyOutgoingSegment] = []
        for seg in message:
            try:
                outgoing.append(self._segment_to_outgoing(seg))
            except (FileNotFoundError, OSError, TypeError, ValueError) as exc:
                raise errors.ArgsInvalidError(f"Invalid Milky outgoing segment {seg}: {exc}") from exc
        if len(outgoing) == 0:
            raise errors.ArgsInvalidError("Milky message must contain at least one supported segment.")

        if group_id is not None:
            scene = 1
            peer_id = self._numeric_id(group_id, "group_id")
            endpoint = "send_group_message"
            payload = {"group_id": peer_id, "message": outgoing}
        elif user_id is not None:
            scene = 0
            peer_id = self._numeric_id(user_id, "user_id")
            endpoint = "send_private_message"
            payload = {"user_id": peer_id, "message": outgoing}
        else:
            raise errors.ArgsInvalidError("'send' API requires 'group_id' or 'user_id' but none of them are provided.")

        chunk_payloads = self._text_chunk_payloads(payload)
        if chunk_payloads is not None:
            logger.warning(f"Milky text message is too long; split into {len(chunk_payloads)} chunks")
            packet, res = self._send_text_chunks(endpoint, chunk_payloads)
        else:
            packet = Packet(endpoint, **payload)
            res = packet.send_to(self.connection)

        if self._is_payload_rejection(res) and any(
                isinstance(item, dict) and item.get("type") == "reply" for item in outgoing
        ):
            fallback_outgoing = [i for i in outgoing if not (isinstance(i, dict) and i.get("type") == "reply")]
            if fallback_outgoing:
                fallback_payload = payload.copy()
                fallback_payload["message"] = fallback_outgoing
                fallback_chunks = self._text_chunk_payloads(fallback_payload, keep_reply=False)
                if fallback_chunks is not None:
                    fallback_packet, fallback_res = self._send_text_chunks(endpoint, fallback_chunks)
                else:
                    fallback_packet = Packet(endpoint, **fallback_payload)
                    fallback_res = fallback_packet.send_to(self.connection)
                if self._is_successful_response(fallback_res):
                    packet, res = fallback_packet, fallback_res

        if not self._is_successful_response(res):
            recovery_payload = payload.copy()
            recovery_payload["message"] = [
                item for item in outgoing
                if not (isinstance(item, dict) and item.get("type") == "reply")
            ]
            recovery_chunks = self._text_chunk_payloads(
                recovery_payload,
                keep_reply=False,
                limit=MILKY_TEXT_RECOVERY_CHUNK_LIMIT,
            )
            if recovery_chunks is not None:
                logger.warning(
                    f"Milky text send failed; retrying as {len(recovery_chunks)} smaller chunks"
                )
                recovery_packet, recovery_res = self._send_text_chunks(
                    endpoint, recovery_chunks, interval=0.2
                )
                if self._is_successful_response(recovery_res):
                    packet, res = recovery_packet, recovery_res

        target = f"群 {group_id}" if group_id is not None else f"用户 {user_id}"
        if not self._is_successful_response(res):
            logger.error(
                f"向{target}发送失败（{endpoint}）："
                f"status={res.get('status') if isinstance(res, dict) else None!r}, "
                f"retcode={res.get('retcode') if isinstance(res, dict) else None!r}, "
                f"message={(res.get('message') or res.get('msg')) if isinstance(res, dict) else res!r}, "
                f"data={res.get('data') if isinstance(res, dict) else None!r}"
            )
            raise errors.ActionFailedError(f"Milky send failed via {endpoint}: {res}")

        data = res.get("data") if isinstance(res, dict) else None
        if not isinstance(data, dict) or data.get("message_seq") is None:
            raise errors.ActionFailedError(f"Milky send returned no message_seq via {endpoint}: {res}")
        data["message_id"] = msg_enid(scene, int(data["message_seq"]), peer_id)
        logger.info(f"向{target}发送：{str(message)}")
        return _fetch_ret(packet.echo, MsgSendRsp)

    async def del_message(self, message_id: ExternalId) -> None:
        enid = self._numeric_id(message_id, "message_id")
        if enid < (1 << 64):
            raise errors.ArgsInvalidError(
                "Milky recall requires an encoded message_id containing scene, peer_id, and message_seq."
            )

        scene, seq, peer_id = msg_deid(enid)
        if scene == 1:
            self._send_action(
                "recall_group_message",
                group_id=peer_id,
                message_seq=seq
            )
        elif scene == 0:
            self._send_action(
                "recall_private_message",
                user_id=peer_id,
                message_seq=seq
            )
        else:
            raise errors.ArgsInvalidError(f"Unsupported Milky message scene: {scene}")
        logger.info(f"撤回 {message_id}")

    async def set_group_kick(self, group_id: ExternalId, user_id: ExternalId) -> None:
        self._send_action(
            "kick_group_member",
            group_id=self._numeric_id(group_id, "group_id"),
            user_id=self._numeric_id(user_id, "user_id"),
            reject_add_request=True,
        )
        logger.info(f"将用户 {user_id} 移出群 {group_id}")

    async def set_group_ban(self, group_id: ExternalId, user_id: ExternalId, duration: int = 60) -> None:
        self._send_action(
            "set_group_member_mute",
            group_id=self._numeric_id(group_id, "group_id"),
            user_id=self._numeric_id(user_id, "user_id"),
            duration=int(duration),
        )
        logger.info(f"在群 {group_id} 将用户 {user_id} 禁言 {duration}s")

    async def get_login_info(self) -> common.Ret[GetLoginInfoRsp]:
        packet = Packet("get_login_info")
        res = packet.send_to(self.connection)
        if isinstance(res, dict) and isinstance(res.get("data"), dict):
            data = res["data"]
            if data.get("user_id") is None and data.get("uin") is not None:
                data["user_id"] = int(data["uin"])
        return _fetch_ret(packet.echo, GetLoginInfoRsp)

    async def get_version_info(self) -> common.Ret[GetVerInfoRsp]:
        packet = Packet("get_impl_info")
        res = packet.send_to(self.connection)
        if isinstance(res, dict) and isinstance(res.get("data"), dict):
            data = res["data"]
            data["app_name"] = data.get("impl_name", "")
            data["app_version"] = data.get("impl_version", "")
            data["protocol_version"] = data.get("milky_version", "")
        return _fetch_ret(packet.echo, GetVerInfoRsp)

    async def send_forward_msg(self, message: common.Message) -> common.Ret[SendForwardRsp]:
        raise errors.ArgsInvalidError(
            "Milky send_forward_msg requires a target; use send_group_forward_msg for group messages."
        )

    async def get_forward_msg(self, sid: str) -> common.Ret[common.Message]:
        packet = Packet("get_forwarded_messages", forward_id=str(sid))
        res = packet.send_to(self.connection)
        if isinstance(res, dict) and isinstance(res.get("data"), dict):
            nodes = []
            for item in res["data"].get("messages", []):
                if not isinstance(item, dict):
                    continue
                onebot_segments = message_translator(
                    consume_segments(item.get("segments")), peer_id=0, scene=0
                )
                nodes.append(segments.Node(
                    str(item.get("user_id", 0)),
                    str(item.get("sender_name", "")),
                    events.gen_message({"message": onebot_segments}),
                ))
            res["data"] = common.Message(*nodes)
        return _fetch_ret(packet.echo, lambda data: data)

    async def forward_solve(self, message: common.Message) -> common.Message:
        return message

    async def send_group_forward_msg(self, group_id: ExternalId, message: common.Message) -> common.Ret[SendGrpForwardRsp]:
        nodes = []
        for node in message:
            if isinstance(node, segments.CustomNode):
                node_data = node.to_json().get("data", {})
                user_id = node_data.get("user_id")
                sender_name = node_data.get("sender_name") or node_data.get("nickname") or node_data.get("nick_name")
                content = node_data.get("content", [])
            elif isinstance(node, segments.Node):
                user_id = node.user_id
                sender_name = node.nickname
                content = node.content.get_sync() if isinstance(node.content, common.Message) else node.content
            else:
                raise errors.ArgsInvalidError(
                    f"Milky forward messages require Node or CustomNode segments, got {type(node).__name__}."
                )
            if not isinstance(content, list) or len(content) == 0:
                raise errors.ArgsInvalidError("Milky forward node content must not be empty.")
            outgoing_segments = [self._json_segment_to_outgoing(item) for item in content]
            nodes.append(MilkyOutGoingSegBuilder.outgoing_forward(
                int(user_id), str(sender_name or user_id), outgoing_segments
            ))

        if len(nodes) == 0:
            raise errors.ArgsInvalidError("Milky forward message must contain at least one node.")
        outgoing = MilkyOutGoingSegBuilder().forward(nodes).build()
        packet, res = self._send_action(
            "send_group_message",
            group_id=self._numeric_id(group_id, "group_id"),
            message=outgoing,
        )
        data = res.get("data") if isinstance(res, dict) else None
        if not isinstance(data, dict) or data.get("message_seq") is None:
            raise errors.ActionFailedError(f"Milky forward send returned no message_seq: {res}")
        data["message_id"] = msg_enid(
            1,
            int(data["message_seq"]),
            self._numeric_id(group_id, "group_id"),
        )
        data.setdefault("forward_id", "")
        return _fetch_ret(packet.echo, SendGrpForwardRsp)

    async def set_group_add_request(self, flag: str, sub_type: str, approve: bool,
                                    reason: str = "Not Mentioned") -> None:
        raise errors.ArgsInvalidError(
            "Milky group-request handling requires notification_seq, notification_type, "
            "group_id, and is_filtered; the legacy OneBot flag signature cannot represent them."
        )

    async def get_stranger_info(self, user_id: ExternalId) -> common.Ret[GetStrInfoRsp]:
        numeric_user_id = self._numeric_id(user_id, "user_id")
        packet = Packet("get_user_profile", user_id=numeric_user_id)
        res = packet.send_to(self.connection)
        if isinstance(res, dict) and isinstance(res.get("data"), dict):
            data = res["data"]
            data["user_id"] = data.get("user_id") or data.get("userId") or data.get("uid") or numeric_user_id
            data["nickname"] = data.get("nickname") or data.get("nick") or data.get("name") or ""
            data["sex"] = data.get("sex") or "unknown"
            try:
                data["age"] = int(data.get("age") or data.get("qage") or data.get("qq_age") or 0)
            except (TypeError, ValueError):
                data["age"] = 0
        return _fetch_ret(packet.echo, GetStrInfoRsp)

    async def get_group_member_info(self, group_id: ExternalId, user_id: ExternalId) -> common.Ret[GetGrpMemInfoRsp]:
        numeric_group_id = self._numeric_id(group_id, "group_id")
        numeric_user_id = self._numeric_id(user_id, "user_id")
        packet = Packet(
            "get_group_member_info",
            group_id=numeric_group_id,
            user_id=numeric_user_id,
        )
        res = packet.send_to(self.connection)
        if isinstance(res, dict) and isinstance(res.get("data"), dict):
            wrapper = res["data"]
            member = wrapper.get("member") if isinstance(wrapper.get("member"), dict) else wrapper
            normalized = {
                "group_id": int(member.get("group_id") or numeric_group_id),
                "user_id": int(member.get("user_id") or numeric_user_id),
                "nickname": member.get("nickname") or member.get("name") or "",
                "card": member.get("card") or "",
                "sex": member.get("sex") or "unknown",
                "age": member.get("age") or 0,
                "area": member.get("area") or "",
                "join_time": int(member.get("join_time") or 0),
                "last_sent_time": int(member.get("last_sent_time") or 0),
                "level": str(member.get("level") or ""),
                "role": member.get("role") or "member",
                "unfriendly": bool(member.get("unfriendly", False)),
                "title": member.get("title") or "",
                "title_expire_time": int(member.get("title_expire_time") or 0),
                "card_changeable": bool(member.get("card_changeable", True)),
            }
            wrapper.clear()
            wrapper.update(normalized)
        return _fetch_ret(packet.echo, GetGrpMemInfoRsp)

    async def get_group_info(self, group_id: ExternalId) -> common.Ret[GetGrpInfoRsp]:
        numeric_group_id = self._numeric_id(group_id, "group_id")
        packet = Packet(
            "get_group_info",
            group_id=numeric_group_id,
        )
        res = packet.send_to(self.connection)
        if isinstance(res, dict) and isinstance(res.get("data"), dict):
            wrapper = res["data"]
            group = wrapper.get("group") if isinstance(wrapper.get("group"), dict) else wrapper
            normalized = {
                "group_id": int(group.get("group_id") or numeric_group_id),
                "group_name": group.get("group_name") or group.get("name") or "",
                "member_count": int(group.get("member_count") or 0),
                "max_member_count": int(group.get("max_member_count") or 0),
            }
            wrapper.clear()
            wrapper.update(normalized)
        return _fetch_ret(packet.echo, GetGrpInfoRsp)

    async def get_status(self) -> common.Ret:
        packet = Packet("get_login_info")
        res = packet.send_to(self.connection)
        if isinstance(res, dict) and self._is_successful_response(res):
            res["data"] = {"online": True, "good": True}
        return _fetch_ret(packet.echo)

    async def set_essence_msg(self, message_id: ExternalId) -> None:
        enid = self._numeric_id(message_id, "message_id")
        if enid < (1 << 64):
            raise errors.ArgsInvalidError(
                "Milky essence actions require an encoded group message_id."
            )
        scene, seq, peer_id = msg_deid(enid)
        if scene != 1:
            raise errors.ArgsInvalidError("Only group messages can be marked as essence messages.")
        self._send_action(
            "set_group_essence_message",
            group_id=int(peer_id),
            message_seq=int(seq),
            is_set=True,
        )

    async def set_group_special_title(self, group_id: ExternalId, user_id: ExternalId, title: str) -> None:
        self._send_action(
            "set_group_member_special_title",
            group_id=self._numeric_id(group_id, "group_id"),
            user_id=self._numeric_id(user_id, "user_id"),
            special_title=str(title),
        )

    def _get_msg_sync(
            self,
            msg_id: ExternalId,
            *,
            timeout_seconds: float = 15.0,
            attempts: int = 3,
    ) -> common.Ret[GetMsgRsp]:
        enid = self._numeric_id(msg_id, "message_id")
        if enid < (1 << 64):
            raise errors.ArgsInvalidError(
                "Milky get_msg requires an encoded message_id containing scene, peer_id, and message_seq."
            )
        scene, seq, peer_id = msg_deid(enid)
        if scene not in (0, 1):
            raise errors.ArgsInvalidError(f"Unsupported Milky message scene: {scene}")
        packet = Packet(
            "get_message",
            message_scene="group" if scene == 1 else "friend",
            peer_id=int(peer_id),
            message_seq=int(seq),
        )
        res = packet.send_to(
            self.connection,
            timeout_seconds=timeout_seconds,
            attempts=attempts,
        )
        if isinstance(res, dict) and isinstance(res.get("data"), dict):
            data = res["data"]
            if isinstance(data.get("message"), dict):
                message_data = data["message"]
                for key in (
                        "time", "message_scene", "peer_id", "message_seq", "sender_id",
                        "segments", "friend", "group_member"
                ):
                    if data.get(key) is None and message_data.get(key) is not None:
                        data[key] = message_data.get(key)
            scene_value = data.get("message_scene") or data.get("scene") or data.get("message_type")
            if scene_value in ("friend", "private", 0, "0"):
                scene = 0
                message_type = "private"
            elif scene_value in ("group", 1, "1"):
                scene = 1
                message_type = "group"
            else:
                scene = None
                message_type = data.get("message_type")

            peer_id = data.get("peer_id") or data.get("group_id") or data.get("user_id")
            message_seq = data.get("message_seq") or data.get("seq") or data.get("real_id")
            if data.get("message_id") is None and scene is not None and message_seq is not None and peer_id is not None:
                data["message_id"] = int(msg_enid(scene, int(message_seq), int(peer_id)))

            data["real_id"] = data.get("real_id") or int(message_seq or 0)
            data["time"] = data.get("time") or data.get("timestamp") or int(time.time())
            if message_type is not None:
                data["message_type"] = message_type

            if data.get("sender") is None:
                if message_type == "group":
                    sender = data.get("group_member") or data.get("member") or {}
                    data["sender"] = {
                        "user_id": int(data.get("sender_id") or data.get("user_id") or 0),
                        "nickname": sender.get("nickname") or sender.get("name") or "",
                        "card": sender.get("card") or "",
                        "sex": sender.get("sex") or "unknown",
                        "age": 0,
                        "area": "",
                        "level": str(sender.get("level") or ""),
                        "role": sender.get("role") or "member",
                        "title": sender.get("title") or ""
                    }
                else:
                    sender = data.get("friend") or data.get("sender") or {}
                    data["sender"] = {
                        "user_id": int(data.get("sender_id") or data.get("user_id") or 0),
                        "nickname": sender.get("nickname") or sender.get("name") or "",
                        "sex": sender.get("sex") or "unknown",
                        "age": 0
                    }

            milky_segments = consume_segments(data.get("segments"))
            data["message"] = message_translator(
                milky_segments,
                int(peer_id or 0),
                int(scene if scene is not None else (1 if message_type == "group" else 0)),
            )
        return _fetch_ret(packet.echo, GetMsgRsp)

    async def get_msg(self, msg_id: ExternalId) -> common.Ret[GetMsgRsp]:
        return self._get_msg_sync(msg_id)

    async def send_callback(self, group_id: ExternalId, bot_id: ExternalId, data: dict) -> None:
        raise errors.ArgsInvalidError("Milky does not define a send_callback API.")

    async def resolve_reference(
            self,
            message_id: ExternalId,
            *,
            conversation: ConversationKey,
            timeout_seconds: float = 10.0,
    ) -> ReferenceResolution:
        if conversation.protocol != self.protocol:
            return reference_failure(
                ResolutionStatus.REJECTED,
                ResolutionErrorCode.CONVERSATION_MISMATCH,
                message_id=message_id,
                conversation=conversation,
            )
        try:
            encoded_id = numeric_external_id(message_id, "message_id")
        except ValueError:
            return reference_failure(
                ResolutionStatus.REJECTED,
                ResolutionErrorCode.INVALID_ID,
                message_id=message_id,
                conversation=conversation,
            )
        if encoded_id < (1 << 64):
            return reference_failure(
                ResolutionStatus.REJECTED,
                ResolutionErrorCode.INVALID_ID,
                message_id=message_id,
                conversation=conversation,
            )
        scene, _, peer_id = msg_deid(encoded_id)
        if scene == 1:
            resolved_kind = ConversationKind.GROUP
        elif scene == 0:
            resolved_kind = ConversationKind.PRIVATE
        else:
            return reference_failure(
                ResolutionStatus.REJECTED,
                ResolutionErrorCode.INVALID_ID,
                message_id=message_id,
                conversation=conversation,
            )
        if resolved_kind is not conversation.kind or str(peer_id) != conversation.conversation_id:
            return reference_failure(
                ResolutionStatus.REJECTED,
                ResolutionErrorCode.CONVERSATION_MISMATCH,
                message_id=message_id,
                conversation=conversation,
            )
        try:
            timeout_seconds = positive_timeout_seconds(timeout_seconds)
        except ValueError:
            return reference_failure(
                ResolutionStatus.REJECTED,
                ResolutionErrorCode.INVALID_TIMEOUT,
                message_id=message_id,
                conversation=conversation,
            )
        try:
            response = await asyncio.wait_for(
                asyncio.to_thread(
                    self._get_msg_sync,
                    str(encoded_id),
                    timeout_seconds=timeout_seconds,
                    attempts=1,
                ),
                timeout=timeout_seconds,
            )
        except asyncio.TimeoutError:
            return reference_failure(
                ResolutionStatus.ERROR,
                ResolutionErrorCode.TIMEOUT,
                message_id=message_id,
                conversation=conversation,
            )
        except Exception:
            return reference_failure(
                ResolutionStatus.ERROR,
                ResolutionErrorCode.UPSTREAM_ERROR,
                message_id=message_id,
                conversation=conversation,
            )
        if getattr(response, "status", None) != "ok" or getattr(response, "ret_code", None) not in (0, None):
            return reference_failure(
                ResolutionStatus.ERROR,
                ResolutionErrorCode.UPSTREAM_ERROR,
                message_id=message_id,
                conversation=conversation,
            )
        data = getattr(response, "data", None)
        sender = getattr(data, "sender", None)
        return reference_success(
            expected=conversation,
            message_id=message_id,
            resolved_kind=resolved_kind,
            resolved_conversation_id=str(peer_id),
            sender_id=getattr(sender, "user_id", None),
            sent_at=getattr(data, "time", None),
            segments=response_segments(response),
        )

    async def resolve_media(
            self,
            request: MediaRequest,
            *,
            conversation: ConversationKey,
            policy: MediaPolicy = DEFAULT_MEDIA_POLICY,
    ) -> MediaResolution:
        if conversation.protocol != self.protocol:
            return media_failure(
                request,
                ResolutionStatus.REJECTED,
                ResolutionErrorCode.CONVERSATION_MISMATCH,
            )
        return await resolve_media_request(request, policy)


async def tester(
        message_data: Union[Event, HyperNotify], actions: Actions
) -> None:
    ...


def __handler(data: Union[dict, HyperNotify], actions: Actions) -> None:
    if isinstance(data, dict):
        event_data = data.copy()
        event_data["protocol"] = "milky"
        event_data.setdefault(
            "conversation_id",
            event_data.get("group_id") if event_data.get("group_id") not in (None, 0, "0") else event_data.get("user_id"),
        )
        run_awaitable(handler(events.em.new(event_data), actions))
    else:
        run_awaitable(handler(data, actions))


handler: callable = tester


def reg(func: callable) -> None:
    global handler
    handler = func


connection: MilkyHttpConnection


def run() -> NoReturn:
    global connection, listener_ran
    listener_ran = True
    try:
        if handler is tester:
            raise errors.ListenerNotRegisteredError("No handler registered")
        conn_config = config.get_connection("Milky")
        if isinstance(conn_config, configurator.BotWSC):
            connection = MilkyHttpConnection(
                f"ws://{conn_config.host}:{conn_config.port}",
                auth=getattr(conn_config, "auth", None)
            )
        elif isinstance(conn_config, configurator.BotHTTPC):
            connection = MilkyHttpConnection(
                f"ws://{conn_config.host}:{conn_config.port}",
                auth=getattr(conn_config, "auth", None)
            )
        retried = 0

        while listener_ran:
            try:
                connection.connect()
            except (ConnectionRefusedError, TimeoutError):
                if retried >= conn_config.retries:
                    logger.critical(f"重试次数达到最大值({conn_config.retries})，退出")
                    break

                logger.warning(f"连接建立失败，3秒后重试({retried}/{conn_config.retries})")
                retried += 1
                time.sleep(3)
                continue
            retried = 0
            logger.info(f"成功在 {connection.url} 建立连接")
            actions = Actions(connection)
            data = HyperListenerStartNotify(
                time_now=int(time.time()),
                notify_type="listener_start",
                connection=connection
            )
            threading.Thread(target=lambda: __handler(data, actions), daemon=True).start()
            while listener_ran:
                try:
                    data = connection.recv()
                except ConnectionResetError:
                    logger.error("连接断开")
                    break
                except json.decoder.JSONDecodeError:
                    logger.error("收到错误的JSON内容")
                    continue
                threading.Thread(target=lambda: __handler(data, actions), daemon=True).start()
    except KeyboardInterrupt:
        logger.warning("正在退出(Ctrl+C)")
        try:
            connection.close()
        except:
            pass
        sys.exit()


def stop() -> None:
    global listener_ran
    listener_ran = False
    try:
        connection.close()
    except Exception:
        pass

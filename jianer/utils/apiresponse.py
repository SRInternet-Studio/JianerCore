from abc import ABC, abstractmethod
from typing import Union, Literal

from ..events import PrivateSender, GroupSender, gen_message
from ..common import Message
from ..adapters.contracts import ExternalId, normalize_external_id


class BaseResponse(ABC):
    def __init__(self, json_data: Union[dict, str]):
        self.raw = json_data
        if json_data:
            # getattr(self, "inner_build")(json_data)
            self.inner_build(json_data)

    def inner_build(self, json_data: Union[dict, str]):
        ...

    @classmethod
    def build(cls, *args, **kwargs):
        ...


class MsgSendRsp(BaseResponse):
    message_id: ExternalId

    def inner_build(self, json_data: dict):
        self.message_id = normalize_external_id(json_data["message_id"], "message_id")

    @classmethod
    def build(cls, message_id: ExternalId):
        return cls({"message_id": message_id})


class GetLoginInfoRsp(BaseResponse):
    user_id: ExternalId
    nickname: str

    def inner_build(self, json_data: dict):
        self.user_id = normalize_external_id(json_data["user_id"], "user_id")
        self.nickname = json_data["nickname"]

    @classmethod
    def build(cls, user_id: ExternalId, nickname: str):
        return cls({"user_id": user_id, "nickname": nickname})


class GetVerInfoRsp(BaseResponse):
    app_name: str
    app_version: str
    protocol_version: str

    def inner_build(self, json_data: dict):
        self.app_name = json_data["app_name"]
        self.app_version = json_data["app_version"]
        self.protocol_version = json_data["protocol_version"]

    @classmethod
    def build(cls, app_name: str, app_version: str, protocol_version: str):
        return cls({"app_name": app_name, "app_version": app_version, "protocol_version": protocol_version})


class SendForwardRsp(BaseResponse):
    res_id: str

    def inner_build(self, json_data: str):
        self.res_id = str(json_data)

    @classmethod
    def build(cls, res_id: str):
        return cls(res_id)


class SendGrpForwardRsp(BaseResponse):
    message_id: ExternalId
    forward_id: str

    def inner_build(self, json_data: dict):
        self.message_id = normalize_external_id(json_data["message_id"], "message_id")
        self.forward_id = str(json_data.get("forward_id") or "")

    @classmethod
    def build(cls, message_id: ExternalId, forward_id: ExternalId):
        return cls({"message_id": message_id, "forward_id": forward_id})


class GetStrInfoRsp(BaseResponse):
    user_id: ExternalId
    nickname: str
    sex: str
    age: int

    def inner_build(self, json_data: dict):
        self.user_id = normalize_external_id(json_data["user_id"], "user_id")
        self.nickname = json_data["nickname"]
        self.sex = json_data["sex"]
        self.age = json_data["age"]

    @classmethod
    def build(cls, user_id: ExternalId, nickname: str, sex: str, age: int):
        return cls({"user_id": user_id, "nickname": nickname, "sex": sex, "age": age})


class GetGrpMemInfoRsp(BaseResponse):
    group_id: ExternalId
    user_id: ExternalId
    nickname: str
    card: str
    sex: str
    age: str
    area: str
    join_time: int
    last_sent_time: int
    level: str
    role: Literal["owner", "admin", "member"]
    unfriendly: bool
    title: str
    title_expire_time: int
    card_changeable: bool

    def inner_build(self, json_data: dict):
        self.group_id = normalize_external_id(json_data["group_id"], "group_id")
        self.user_id = normalize_external_id(json_data["user_id"], "user_id")
        self.nickname = json_data["nickname"]
        self.card = json_data["card"]
        self.sex = json_data["sex"]
        self.age = json_data["age"]
        self.area = json_data["area"]
        self.join_time = json_data["join_time"]
        self.last_sent_time = json_data["last_sent_time"]
        self.level = json_data["level"]
        self.role = json_data["role"]
        self.unfriendly = json_data["unfriendly"]
        self.title = json_data["title"]
        self.title_expire_time = json_data["title_expire_time"]
        self.card_changeable = json_data["card_changeable"]

    @classmethod
    def build(cls, **kwargs):
        return cls(**kwargs)


class GetGrpInfoRsp(BaseResponse):
    group_id: ExternalId
    group_name: str
    member_count: int
    max_member_count: int

    def inner_build(self, json_data: dict):
        self.group_id = normalize_external_id(json_data["group_id"], "group_id")
        self.group_name = json_data["group_name"]
        self.member_count = json_data["member_count"]
        self.max_member_count = json_data["max_member_count"]

    @classmethod
    def build(cls, group_id: ExternalId, group_name: str, member_count: int, max_member_count: int):
        return cls(
            {
                "group_id": group_id,
                "group_name": group_name,
                "member_count": member_count,
                "max_member_count": max_member_count
            }
        )


class GetMsgRsp(BaseResponse):
    time: int
    message_type: Literal["private", "group"]
    message_id: ExternalId
    real_id: ExternalId
    sender: Union[PrivateSender, GroupSender]
    message: Message

    def inner_build(self, json_data: dict):
        self.time = json_data["time"]
        self.message_type = json_data["message_type"]
        self.message_id = normalize_external_id(json_data["message_id"], "message_id")
        self.real_id = normalize_external_id(json_data["real_id"], "real_id")
        self.sender = GroupSender(
            json_data["sender"]
        ) if self.message_type == "group" else PrivateSender(
            json_data["sender"]
        )
        self.message = gen_message(json_data)

    @classmethod
    def build(
            cls, time: int, message_type: Literal["private", "group"], message_id: ExternalId,
            real_id: ExternalId, sender: dict,
            message: dict
    ):
        return cls(
            {
                "time": time,
                "message_type": message_type,
                "message_id": message_id,
                "real_id": real_id,
                "sender": sender,
                "message": message
            }
        )

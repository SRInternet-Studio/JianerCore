import asyncio

import pytest

from jianer import common, segments
from jianer.LecAdapters.Milky import Actions
from jianer.LecAdapters.MilkyLib.translator import MilkyHttpConnection, msg_enid
from jianer.utils import errors


def _actions_with_responses(monkeypatch, responder):
    connection = MilkyHttpConnection("ws://127.0.0.1:3000")
    calls = []

    def fake_http_send(endpoint, data):
        calls.append((endpoint, data))
        return responder(endpoint, data)

    monkeypatch.setattr(connection, "http_send", fake_http_send)
    return Actions(connection), calls


def test_milky_getters_normalize_official_wrapped_entities(monkeypatch):
    def responder(endpoint, data):
        if endpoint == "get_login_info":
            return {"status": "ok", "retcode": 0, "data": {"uin": 12345, "nickname": "bot"}}
        if endpoint == "get_group_info":
            return {
                "status": "ok",
                "retcode": 0,
                "data": {"group": {
                    "group_id": 10001,
                    "group_name": "test group",
                    "member_count": 2,
                    "max_member_count": 200,
                }},
            }
        if endpoint == "get_group_member_info":
            return {
                "status": "ok",
                "retcode": 0,
                "data": {"member": {
                    "group_id": 10001,
                    "user_id": 12345,
                    "nickname": "bot",
                    "sex": "unknown",
                    "card": "",
                    "title": "",
                    "level": 1,
                    "role": "member",
                    "join_time": 10,
                    "last_sent_time": 20,
                }},
            }
        raise AssertionError(endpoint)

    actions, calls = _actions_with_responses(monkeypatch, responder)

    login = asyncio.run(actions.get_login_info())
    group = asyncio.run(actions.get_group_info(10001))
    member = asyncio.run(actions.get_group_member_info(10001, 12345))

    assert login.data.user_id == "12345"
    assert login.data.nickname == "bot"
    assert group.data.group_id == "10001"
    assert group.data.group_name == "test group"
    assert member.data.group_id == "10001"
    assert member.data.user_id == "12345"
    assert member.data.level == "1"
    assert [endpoint for endpoint, _ in calls] == [
        "get_login_info",
        "get_group_info",
        "get_group_member_info",
    ]


def test_milky_get_status_uses_standard_login_api(monkeypatch):
    actions, calls = _actions_with_responses(
        monkeypatch,
        lambda endpoint, data: {
            "status": "ok",
            "retcode": 0,
            "data": {"uin": 12345, "nickname": "bot"},
        },
    )

    response = asyncio.run(actions.get_status())

    assert response.ret_code == 0
    assert response.data.online is True
    assert response.data.good is True
    assert calls == [("get_login_info", {})]


def test_milky_get_msg_uses_official_identity_and_unwraps_message(monkeypatch):
    encoded_id = msg_enid(1, 42, 10001)

    def responder(endpoint, data):
        return {
            "status": "ok",
            "retcode": 0,
            "data": {"message": {
                "time": 1710000000,
                "message_scene": "group",
                "peer_id": 10001,
                "message_seq": 42,
                "sender_id": 12345,
                "segments": [{"type": "text", "data": {"text": "hello"}}],
                "group_member": {
                    "nickname": "tester",
                    "card": "card",
                    "sex": "unknown",
                    "level": 1,
                    "role": "member",
                    "title": "",
                },
            }},
        }

    actions, calls = _actions_with_responses(monkeypatch, responder)

    response = asyncio.run(actions.get_msg(encoded_id))

    assert calls == [("get_message", {
        "message_scene": "group",
        "peer_id": 10001,
        "message_seq": 42,
    })]
    assert response.data.message_id == str(encoded_id)
    assert response.data.real_id == "42"
    assert response.data.message_type == "group"
    assert str(response.data.message) == "hello"


def test_milky_mutations_use_only_standard_endpoints(monkeypatch):
    actions, calls = _actions_with_responses(
        monkeypatch,
        lambda endpoint, data: {"status": "ok", "retcode": 0, "data": {}},
    )
    encoded_id = msg_enid(1, 42, 10001)

    asyncio.run(actions.del_message(encoded_id))
    asyncio.run(actions.set_group_kick(10001, 12345))
    asyncio.run(actions.set_group_ban(10001, 12345, 60))
    asyncio.run(actions.set_essence_msg(encoded_id))
    asyncio.run(actions.set_group_special_title(10001, 12345, "title"))

    assert calls == [
        ("recall_group_message", {"group_id": 10001, "message_seq": 42}),
        ("kick_group_member", {
            "group_id": 10001,
            "user_id": 12345,
            "reject_add_request": True,
        }),
        ("set_group_member_mute", {
            "group_id": 10001,
            "user_id": 12345,
            "duration": 60,
        }),
        ("set_group_essence_message", {
            "group_id": 10001,
            "message_seq": 42,
            "is_set": True,
        }),
        ("set_group_member_special_title", {
            "group_id": 10001,
            "user_id": 12345,
            "special_title": "title",
        }),
    ]


@pytest.mark.parametrize(
    "response",
    [
        {"status": "failed", "retcode": -400, "message": "bad request"},
        {"status": "failed", "retcode": 0, "message": "contradictory status"},
        {"status": "ok", "retcode": -400, "message": "contradictory retcode"},
        {"code": 0, "data": {}},
    ],
)
def test_milky_mutation_failure_is_not_logged_as_success(monkeypatch, response):
    actions, _ = _actions_with_responses(
        monkeypatch,
        lambda endpoint, data: response,
    )

    with pytest.raises(errors.ActionFailedError, match="set_group_member_mute"):
        asyncio.run(actions.set_group_ban(10001, 12345, 60))


def test_milky_group_forward_uses_forward_outgoing_segment(monkeypatch):
    actions, calls = _actions_with_responses(
        monkeypatch,
        lambda endpoint, data: {
            "status": "ok",
            "retcode": 0,
            "data": {"message_seq": 7, "time": 1710000000},
        },
    )
    message = common.Message(
        segments.CustomNode("12345", "bot", common.Message(segments.Text("hello")))
    )

    response = asyncio.run(actions.send_group_forward_msg(10001, message))

    assert response.data.message_id == str(msg_enid(1, 7, 10001))
    assert calls == [("send_group_message", {
        "group_id": 10001,
        "message": [{
            "type": "forward",
            "data": {"messages": [{
                "user_id": 12345,
                "sender_name": "bot",
                "segments": [{"type": "text", "data": {"text": "hello"}}],
            }]},
        }],
    })]


def test_milky_get_forward_msg_translates_forward_nodes(monkeypatch):
    actions, calls = _actions_with_responses(
        monkeypatch,
        lambda endpoint, data: {
            "status": "ok",
            "retcode": 0,
            "data": {"messages": [{
                "user_id": 12345,
                "sender_name": "tester",
                "segments": [{"type": "text", "data": {"text": "hello"}}],
            }]},
        },
    )

    response = asyncio.run(actions.get_forward_msg("forward-1"))

    assert calls == [("get_forwarded_messages", {"forward_id": "forward-1"})]
    assert isinstance(response.data, common.Message)
    assert isinstance(response.data[0], segments.Node)
    assert response.data[0].nickname == "tester"
    assert str(response.data[0].content) == "hello"


def test_milky_custom_action_passes_through_arbitrary_standard_api(monkeypatch):
    actions, calls = _actions_with_responses(
        monkeypatch,
        lambda endpoint, data: {"status": "ok", "retcode": 0, "data": {}},
    )

    echo = asyncio.run(actions.custom.get_friend_list(no_cache=True))

    assert echo.startswith("get_friend_list_")
    assert calls == [("get_friend_list", {"no_cache": True})]


def test_milky_unrepresentable_legacy_actions_fail_explicitly(monkeypatch):
    actions, calls = _actions_with_responses(
        monkeypatch,
        lambda endpoint, data: {"status": "ok", "retcode": 0, "data": {}},
    )
    message = common.Message(segments.Text("hello"))

    assert asyncio.run(actions.forward_solve(message)) is message
    with pytest.raises(errors.ArgsInvalidError, match="requires a target"):
        asyncio.run(actions.send_forward_msg(message))
    with pytest.raises(errors.ArgsInvalidError, match="legacy OneBot flag signature"):
        asyncio.run(actions.set_group_add_request("flag", "add", True))
    with pytest.raises(errors.ArgsInvalidError, match="does not define a send_callback"):
        asyncio.run(actions.send_callback(10001, 12345, {}))

    assert calls == []


def test_milky_legacy_raw_message_ids_are_rejected(monkeypatch):
    actions, calls = _actions_with_responses(
        monkeypatch,
        lambda endpoint, data: {"status": "ok", "retcode": 0, "data": {}},
    )

    with pytest.raises(errors.ArgsInvalidError, match="encoded message_id"):
        asyncio.run(actions.del_message(42))
    with pytest.raises(errors.ArgsInvalidError, match="encoded message_id"):
        asyncio.run(actions.get_msg(42))

    assert calls == []

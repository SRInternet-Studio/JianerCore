from __future__ import annotations

import argparse
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import httpx


JsonObject = dict[str, Any]
PayloadBuilder = Callable[["AuditContext"], JsonObject]


@dataclass
class EndpointCase:
    category: str
    name: str
    payload: PayloadBuilder
    mode: str = "normal"
    note: str = ""


@dataclass
class AuditContext:
    base_url: str
    auth: str
    login_uin: int | None = None
    owner_id: int | None = None
    group_id: int | None = None
    friend_id: int | None = None
    member_id: int | None = None
    sent_group_seq: int | None = None
    sent_private_seq: int | None = None


def _post(ctx: AuditContext, endpoint: str, payload: JsonObject, timeout: float = 15.0) -> JsonObject:
    headers = {"Authorization": f"Bearer {ctx.auth}"} if ctx.auth else {}
    started = time.perf_counter()
    try:
        response = httpx.post(
            f"{ctx.base_url}/api/{endpoint}",
            json=payload,
            headers=headers,
            timeout=timeout,
        )
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        try:
            body = response.json()
        except json.JSONDecodeError:
            body = {
                "status": "failed",
                "retcode": response.status_code,
                "message": "Non-JSON response",
                "data": {"raw": response.text[:500]},
            }
        return {
            "http_status": response.status_code,
            "elapsed_ms": elapsed_ms,
            "body": body,
        }
    except httpx.RequestError as exc:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        return {
            "http_status": None,
            "elapsed_ms": elapsed_ms,
            "body": {"status": "failed", "retcode": -1, "message": str(exc), "data": None},
        }


def _status(result: JsonObject) -> str:
    body = result.get("body")
    if not isinstance(body, dict):
        return "transport_failed"
    if body.get("status") == "ok" or body.get("retcode") == 0:
        return "ok"
    if result.get("http_status") == 404:
        return "missing"
    if result.get("http_status") in (401, 403):
        return "auth_failed"
    if result.get("http_status") and result.get("http_status") >= 500:
        return "server_error"
    return "failed"


def _data(result: JsonObject) -> JsonObject:
    body = result.get("body")
    if isinstance(body, dict) and isinstance(body.get("data"), dict):
        return body["data"]
    return {}


def _first_int(raw: Any, *keys: str) -> int | None:
    if not isinstance(raw, dict):
        return None
    for key in keys:
        value = raw.get(key)
        try:
            if value is not None:
                return int(value)
        except (TypeError, ValueError):
            pass
    return None


def _invalid_id() -> int:
    return 1


def _group(ctx: AuditContext) -> int:
    return int(ctx.group_id or _invalid_id())


def _user(ctx: AuditContext) -> int:
    return int(ctx.friend_id or ctx.owner_id or ctx.login_uin or _invalid_id())


def _member(ctx: AuditContext) -> int:
    return int(ctx.member_id or ctx.owner_id or ctx.login_uin or _invalid_id())


def _bad_group(_: AuditContext) -> int:
    return -1


def _bad_user(_: AuditContext) -> int:
    return -1


def _message_text(text: str) -> list[JsonObject]:
    return [{"type": "text", "data": {"text": text}}]


def _cases() -> list[EndpointCase]:
    invalid = "invalid-probe to avoid mutating account, friend, group, or file state"
    return [
        EndpointCase("system", "get_login_info", lambda c: {}),
        EndpointCase("system", "get_impl_info", lambda c: {}),
        EndpointCase("system", "get_user_profile", lambda c: {"user_id": _user(c)}),
        EndpointCase("system", "get_friend_list", lambda c: {"no_cache": False}),
        EndpointCase("system", "get_friend_info", lambda c: {"user_id": _user(c), "no_cache": False}),
        EndpointCase("system", "get_group_list", lambda c: {"no_cache": False}),
        EndpointCase("system", "get_group_info", lambda c: {"group_id": _group(c), "no_cache": False}),
        EndpointCase("system", "get_group_member_list", lambda c: {"group_id": _group(c), "no_cache": False}),
        EndpointCase("system", "get_group_member_info", lambda c: {
            "group_id": _group(c),
            "user_id": _member(c),
            "no_cache": False,
        }),
        EndpointCase("system", "get_peer_pins", lambda c: {}),
        EndpointCase("system", "set_peer_pin", lambda c: {
            "message_scene": "group",
            "peer_id": _bad_group(c),
            "is_pinned": False,
        }, "invalid_probe", invalid),
        EndpointCase("system", "set_avatar", lambda c: {"uri": "file:///Z:/milky-audit-missing-avatar.png"}, "invalid_probe", invalid),
        EndpointCase("system", "set_nickname", lambda c: {}, "invalid_probe", invalid),
        EndpointCase("system", "set_bio", lambda c: {}, "invalid_probe", invalid),
        EndpointCase("system", "get_custom_face_url_list", lambda c: {}),
        EndpointCase("system", "get_cookies", lambda c: {"domain": "qq.com"}),
        EndpointCase("system", "get_csrf_token", lambda c: {}),
        EndpointCase("message", "send_private_message", lambda c: {
            "user_id": _bad_user(c),
            "message": _message_text("JianerCore Milky API audit private probe"),
        }, "invalid_probe", invalid),
        EndpointCase("message", "send_group_message", lambda c: {
            "group_id": _group(c),
            "message": _message_text("JianerCore Milky API audit group probe"),
        }, "send_probe", "sends one short group message, then later attempts recall if possible"),
        EndpointCase("message", "get_message", lambda c: {
            "message_scene": "group",
            "peer_id": _group(c),
            "message_seq": c.sent_group_seq or 1,
        }),
        EndpointCase("message", "get_history_messages", lambda c: {
            "message_scene": "group",
            "peer_id": _group(c),
            "limit": 1,
        }),
        EndpointCase("message", "get_resource_temp_url", lambda c: {"resource_id": "milky-audit-invalid-resource"}, "invalid_probe", invalid),
        EndpointCase("message", "get_forwarded_messages", lambda c: {"forward_id": "milky-audit-invalid-forward"}, "invalid_probe", invalid),
        EndpointCase("message", "mark_message_as_read", lambda c: {
            "message_scene": "group",
            "peer_id": _group(c),
            "message_seq": c.sent_group_seq or 1,
        }),
        EndpointCase("message", "recall_private_message", lambda c: {
            "user_id": _bad_user(c),
            "message_seq": 1,
        }, "invalid_probe", invalid),
        EndpointCase("message", "recall_group_message", lambda c: {
            "group_id": _group(c),
            "message_seq": c.sent_group_seq or -1,
        }, "cleanup_or_invalid", "recalls the audit group message when send_group_message succeeds"),
        EndpointCase("friend", "send_friend_nudge", lambda c: {"user_id": _bad_user(c), "is_self": False}, "invalid_probe", invalid),
        EndpointCase("friend", "send_profile_like", lambda c: {"user_id": _bad_user(c), "count": 1}, "invalid_probe", invalid),
        EndpointCase("friend", "delete_friend", lambda c: {"user_id": _bad_user(c)}, "invalid_probe", invalid),
        EndpointCase("friend", "get_friend_requests", lambda c: {"limit": 20, "is_filtered": False}),
        EndpointCase("friend", "accept_friend_request", lambda c: {"initiator_uid": "milky-audit-invalid", "is_filtered": False}, "invalid_probe", invalid),
        EndpointCase("friend", "reject_friend_request", lambda c: {
            "initiator_uid": "milky-audit-invalid",
            "is_filtered": False,
            "reason": "audit invalid probe",
        }, "invalid_probe", invalid),
        EndpointCase("group", "set_group_name", lambda c: {"group_id": _bad_group(c), "new_group_name": "milky-audit"}, "invalid_probe", invalid),
        EndpointCase("group", "set_group_avatar", lambda c: {
            "group_id": _bad_group(c),
            "image_uri": "file:///Z:/milky-audit-missing-avatar.png",
        }, "invalid_probe", invalid),
        EndpointCase("group", "set_group_member_card", lambda c: {
            "group_id": _bad_group(c),
            "user_id": _bad_user(c),
            "card": "milky-audit",
        }, "invalid_probe", invalid),
        EndpointCase("group", "set_group_member_special_title", lambda c: {
            "group_id": _bad_group(c),
            "user_id": _bad_user(c),
            "special_title": "audit",
        }, "invalid_probe", invalid),
        EndpointCase("group", "set_group_member_admin", lambda c: {
            "group_id": _bad_group(c),
            "user_id": _bad_user(c),
            "is_set": False,
        }, "invalid_probe", invalid),
        EndpointCase("group", "set_group_member_mute", lambda c: {
            "group_id": _bad_group(c),
            "user_id": _bad_user(c),
            "duration": 0,
        }, "invalid_probe", invalid),
        EndpointCase("group", "set_group_whole_mute", lambda c: {"group_id": _bad_group(c), "is_mute": False}, "invalid_probe", invalid),
        EndpointCase("group", "kick_group_member", lambda c: {
            "group_id": _bad_group(c),
            "user_id": _bad_user(c),
            "reject_add_request": False,
        }, "invalid_probe", invalid),
        EndpointCase("group", "get_group_announcements", lambda c: {"group_id": _group(c)}),
        EndpointCase("group", "send_group_announcement", lambda c: {
            "group_id": _bad_group(c),
            "content": "milky-audit invalid probe",
        }, "invalid_probe", invalid),
        EndpointCase("group", "delete_group_announcement", lambda c: {
            "group_id": _bad_group(c),
            "announcement_id": "milky-audit-invalid",
        }, "invalid_probe", invalid),
        EndpointCase("group", "get_group_essence_messages", lambda c: {
            "group_id": _group(c),
            "page_index": 0,
            "page_size": 10,
        }),
        EndpointCase("group", "set_group_essence_message", lambda c: {
            "group_id": _bad_group(c),
            "message_seq": -1,
            "is_set": False,
        }, "invalid_probe", invalid),
        EndpointCase("group", "quit_group", lambda c: {"group_id": _bad_group(c)}, "invalid_probe", invalid),
        EndpointCase("group", "send_group_message_reaction", lambda c: {
            "group_id": _bad_group(c),
            "message_seq": -1,
            "reaction": "1",
            "reaction_type": "face",
            "is_add": False,
        }, "invalid_probe", invalid),
        EndpointCase("group", "send_group_nudge", lambda c: {"group_id": _bad_group(c), "user_id": _bad_user(c)}, "invalid_probe", invalid),
        EndpointCase("group", "get_group_notifications", lambda c: {"is_filtered": False, "limit": 20}),
        EndpointCase("group", "accept_group_request", lambda c: {
            "notification_seq": -1,
            "notification_type": "join_request",
            "group_id": _bad_group(c),
            "is_filtered": False,
        }, "invalid_probe", invalid),
        EndpointCase("group", "reject_group_request", lambda c: {
            "notification_seq": -1,
            "notification_type": "join_request",
            "group_id": _bad_group(c),
            "is_filtered": False,
            "reason": "audit invalid probe",
        }, "invalid_probe", invalid),
        EndpointCase("group", "accept_group_invitation", lambda c: {"group_id": _bad_group(c), "invitation_seq": -1}, "invalid_probe", invalid),
        EndpointCase("group", "reject_group_invitation", lambda c: {"group_id": _bad_group(c), "invitation_seq": -1}, "invalid_probe", invalid),
        EndpointCase("file", "upload_private_file", lambda c: {
            "user_id": _bad_user(c),
            "file_uri": "file:///Z:/milky-audit-missing-file.txt",
            "file_name": "milky-audit.txt",
        }, "invalid_probe", invalid),
        EndpointCase("file", "upload_group_file", lambda c: {
            "group_id": _bad_group(c),
            "parent_folder_id": "/",
            "file_uri": "file:///Z:/milky-audit-missing-file.txt",
            "file_name": "milky-audit.txt",
        }, "invalid_probe", invalid),
        EndpointCase("file", "get_private_file_download_url", lambda c: {
            "user_id": _bad_user(c),
            "file_id": "milky-audit-invalid",
            "file_hash": "milky-audit-invalid",
        }, "invalid_probe", invalid),
        EndpointCase("file", "get_group_file_download_url", lambda c: {
            "group_id": _bad_group(c),
            "file_id": "milky-audit-invalid",
        }, "invalid_probe", invalid),
        EndpointCase("file", "get_group_files", lambda c: {"group_id": _group(c), "parent_folder_id": "/"}),
        EndpointCase("file", "move_group_file", lambda c: {
            "group_id": _bad_group(c),
            "file_id": "milky-audit-invalid",
            "parent_folder_id": "/",
            "target_folder_id": "/",
        }, "invalid_probe", invalid),
        EndpointCase("file", "rename_group_file", lambda c: {
            "group_id": _bad_group(c),
            "file_id": "milky-audit-invalid",
            "parent_folder_id": "/",
            "new_file_name": "milky-audit-renamed.txt",
        }, "invalid_probe", invalid),
        EndpointCase("file", "delete_group_file", lambda c: {"group_id": _bad_group(c), "file_id": "milky-audit-invalid"}, "invalid_probe", invalid),
        EndpointCase("file", "create_group_folder", lambda c: {"group_id": _bad_group(c), "folder_name": "milky-audit"}, "invalid_probe", invalid),
        EndpointCase("file", "rename_group_folder", lambda c: {
            "group_id": _bad_group(c),
            "folder_id": "milky-audit-invalid",
            "new_folder_name": "milky-audit-renamed",
        }, "invalid_probe", invalid),
        EndpointCase("file", "delete_group_folder", lambda c: {"group_id": _bad_group(c), "folder_id": "milky-audit-invalid"}, "invalid_probe", invalid),
    ]


def _load_context(config_path: Path, group_id: int | None, user_id: int | None) -> AuditContext:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    connection = config["connection"]
    base_url = f"http://{connection['host']}:{connection['port']}"
    owners = config.get("owner") or []
    return AuditContext(
        base_url=base_url,
        auth=connection.get("auth") or connection.get("token") or "",
        owner_id=user_id or (int(owners[0]) if owners else None),
        group_id=group_id,
    )


def _hydrate_context(ctx: AuditContext) -> list[JsonObject]:
    discovery = []
    for endpoint, payload in [
        ("get_login_info", {}),
        ("get_group_list", {"no_cache": False}),
        ("get_friend_list", {"no_cache": False}),
    ]:
        result = _post(ctx, endpoint, payload)
        discovery.append({"endpoint": endpoint, "payload": payload, "result": result, "status": _status(result)})
        data = _data(result)
        if endpoint == "get_login_info":
            ctx.login_uin = _first_int(data, "uin", "user_id")
        elif endpoint == "get_group_list" and ctx.group_id is None:
            groups = data.get("groups") if isinstance(data.get("groups"), list) else []
            if groups:
                ctx.group_id = _first_int(groups[0], "group_id")
        elif endpoint == "get_friend_list":
            friends = data.get("friends") if isinstance(data.get("friends"), list) else []
            if friends:
                ctx.friend_id = _first_int(friends[0], "user_id", "uin")

    if ctx.group_id is not None:
        result = _post(ctx, "get_group_member_list", {"group_id": ctx.group_id, "no_cache": False})
        discovery.append({
            "endpoint": "get_group_member_list",
            "payload": {"group_id": ctx.group_id, "no_cache": False},
            "result": result,
            "status": _status(result),
        })
        members = _data(result).get("members")
        if isinstance(members, list):
            for member in members:
                member_id = _first_int(member, "user_id", "uin")
                if member_id and member_id != ctx.login_uin:
                    ctx.member_id = member_id
                    break
            if ctx.member_id is None and members:
                ctx.member_id = _first_int(members[0], "user_id", "uin")
    return discovery


def _redact_result(result: JsonObject) -> JsonObject:
    body = result.get("body")
    if isinstance(body, dict) and isinstance(body.get("data"), dict):
        data = body["data"].copy()
        for key in ("cookies", "csrf_token"):
            if key in data:
                data[key] = "<redacted>"
        body = body.copy()
        body["data"] = data
        result = result.copy()
        result["body"] = body
    return result


def run_audit(ctx: AuditContext) -> JsonObject:
    discovery = _hydrate_context(ctx)
    results = []
    for case in _cases():
        payload = case.payload(ctx)
        result = _post(ctx, case.name, payload)
        status = _status(result)
        data = _data(result)
        if case.name == "send_group_message" and status == "ok":
            ctx.sent_group_seq = _first_int(data, "message_seq")
        if case.name == "send_private_message" and status == "ok":
            ctx.sent_private_seq = _first_int(data, "message_seq")
        results.append({
            "category": case.category,
            "endpoint": case.name,
            "mode": case.mode,
            "note": case.note,
            "payload": payload,
            "status": status,
            "result": _redact_result(result),
        })

    summary: dict[str, int] = {}
    for item in results:
        summary[item["status"]] = summary.get(item["status"], 0) + 1
    return {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "base_url": ctx.base_url,
        "context": {
            "login_uin": ctx.login_uin,
            "owner_id": ctx.owner_id,
            "group_id": ctx.group_id,
            "friend_id": ctx.friend_id,
            "member_id": ctx.member_id,
            "sent_group_seq": ctx.sent_group_seq,
            "sent_private_seq": ctx.sent_private_seq,
        },
        "summary": summary,
        "discovery": discovery,
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit Milky API endpoints listed in the Milky documentation.")
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--group-id", type=int, default=int(os.getenv("MILKY_AUDIT_GROUP_ID", "0")) or None)
    parser.add_argument("--user-id", type=int, default=int(os.getenv("MILKY_AUDIT_USER_ID", "0")) or None)
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    ctx = _load_context(Path(args.config), args.group_id, args.user_id)
    report = run_audit(ctx)
    out = Path(args.out) if args.out else Path("dist") / f"milky_api_audit_{int(time.time())}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Report: {out}")
    print(f"Context: {json.dumps(report['context'], ensure_ascii=False)}")
    print(f"Summary: {json.dumps(report['summary'], ensure_ascii=False)}")
    for item in report["results"]:
        body = item["result"].get("body") or {}
        retcode = body.get("retcode") if isinstance(body, dict) else None
        message = body.get("message") or body.get("msg") if isinstance(body, dict) else None
        print(f"{item['category']}/{item['endpoint']}: {item['status']} retcode={retcode} message={message}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

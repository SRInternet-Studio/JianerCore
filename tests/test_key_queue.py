import asyncio
import json
import threading
import time

import pytest

from jianer import common  # noqa: F401  (initializes jianer-bot config)
from jianer.LecAdapters import OneBot
from jianer.LecAdapters.FeishuLib.Manager import Packet as FeishuPacket
from jianer.LecAdapters.MilkyLib.Manager import Packet as MilkyPacket
from jianer.LecAdapters.OneBotLib.Manager import Packet as OneBotPacket
from jianer.network import WebsocketConnection
from jianer.utils.logic import KeyQueue


def test_key_queue_get_consumes_value():
    queue = KeyQueue()
    queue.put("echo", {"user_id": 1})

    assert queue.get("echo") == {"user_id": 1}
    assert "echo" not in queue.contents
    with pytest.raises(TimeoutError):
        queue.get("echo", timeout=0.05)


def test_key_queue_put_overwrites_unclaimed_value():
    queue = KeyQueue()
    queue.put("echo", "old")
    queue.put("echo", "new")

    assert queue.get("echo") == "new"


def test_key_queue_get_waits_for_threaded_put():
    queue = KeyQueue()
    threading.Timer(0.05, queue.put, args=("echo", "value")).start()

    assert queue.get("echo", timeout=2) == "value"


def test_key_queue_get_times_out():
    queue = KeyQueue()
    started = time.monotonic()

    with pytest.raises(TimeoutError):
        queue.get("missing", timeout=0.1)
    assert time.monotonic() - started >= 0.1


def test_key_queue_expires_unclaimed_values():
    queue = KeyQueue(ttl=0.05)
    queue.put("stale", "value")
    time.sleep(0.1)
    queue.put("fresh", "value")

    assert "stale" not in queue.contents
    assert "fresh" in queue.contents


def test_key_queue_get_async_does_not_block_event_loop():
    queue = KeyQueue()
    ticks = []

    async def ticker():
        for i in range(5):
            ticks.append(i)
            await asyncio.sleep(0.01)

    async def main():
        threading.Timer(0.1, queue.put, args=("echo", "value")).start()
        value, _ = await asyncio.gather(queue.get_async("echo", timeout=2), ticker())
        return value

    assert asyncio.run(main()) == "value"
    assert ticks == [0, 1, 2, 3, 4]
    assert not queue._async_waiters


def test_key_queue_get_async_times_out():
    queue = KeyQueue()

    with pytest.raises(TimeoutError):
        asyncio.run(queue.get_async("missing", timeout=0.05))
    assert not queue._async_waiters


@pytest.mark.parametrize("packet_cls", [OneBotPacket, MilkyPacket, FeishuPacket])
def test_packet_echoes_are_unique(packet_cls):
    echoes = {packet_cls("get_stranger_info").echo for _ in range(20000)}

    assert len(echoes) == 20000


class _FakeWebsocket(WebsocketConnection):
    def __init__(self, respond):
        self.respond = respond

    def send(self, message: str) -> None:
        payload = json.loads(message)
        threading.Timer(0.02, self.respond, args=(payload,)).start()


def test_onebot_responses_are_routed_by_echo_and_consumed():
    def respond(payload):
        user_id = payload["params"]["user_id"]
        OneBot.reports.put(payload["echo"], {
            "status": "ok",
            "retcode": 0,
            "data": {"user_id": user_id, "nickname": f"user-{user_id}", "sex": "unknown", "age": 0},
            "echo": payload["echo"],
        })

    actions = OneBot.Actions(_FakeWebsocket(respond))

    async def main():
        return await asyncio.gather(*(actions.get_stranger_info(uid) for uid in range(10001, 10021)))

    results = asyncio.run(main())

    assert [r.data.nickname for r in results] == [f"user-{uid}" for uid in range(10001, 10021)]
    assert not any(k.startswith("get_stranger_info_") for k in OneBot.reports.contents)

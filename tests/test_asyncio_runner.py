import asyncio
import threading
from types import SimpleNamespace

import pytest

from jianer import (
    Client,
    get_dispatch_runner,
    request_dispatch_runner_shutdown,
    run_awaitable,
    shutdown_dispatch_runner,
)
from jianer.LecAdapters import Feishu, Kritor, Milky, OneBot
from jianer.plugins import PluginManager


@pytest.fixture(autouse=True)
def clean_dispatch_runner():
    assert shutdown_dispatch_runner(timeout=5)
    yield
    assert shutdown_dispatch_runner(timeout=5)


def _run_threads(calls):
    errors = []

    def invoke(call):
        try:
            call()
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=invoke, args=(call,)) for call in calls]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(5)
        assert not thread.is_alive()
    assert errors == []


def test_all_adapter_events_share_one_concurrent_loop_and_asyncio_lock(monkeypatch):
    state = {
        "loop_ids": [],
        "concurrent": 0,
        "max_concurrent": 0,
        "locked": 0,
        "max_locked": 0,
        "lock": None,
    }

    async def capture(_event, _actions):
        state["loop_ids"].append(id(asyncio.get_running_loop()))
        state["concurrent"] += 1
        state["max_concurrent"] = max(
            state["max_concurrent"],
            state["concurrent"],
        )
        await asyncio.sleep(0.03)
        state["concurrent"] -= 1

        if state["lock"] is None:
            state["lock"] = asyncio.Lock()
        async with state["lock"]:
            state["locked"] += 1
            state["max_locked"] = max(state["max_locked"], state["locked"])
            await asyncio.sleep(0.01)
            state["locked"] -= 1

    for adapter in (OneBot, Milky, Feishu, Kritor):
        monkeypatch.setattr(adapter, "handler", capture)

    marker = SimpleNamespace(name="event")
    actions = SimpleNamespace()
    _run_threads(
        [
            lambda: OneBot.__handler(marker, actions),
            lambda: Milky.__handler(marker, actions),
            lambda: Feishu.__handler(marker, actions),
            lambda: Kritor._handler(marker, actions),
        ]
    )

    assert len(set(state["loop_ids"])) == 1
    assert state["max_concurrent"] > 1
    assert state["max_locked"] == 1


def test_background_task_survives_between_short_lived_event_threads():
    started = threading.Event()
    state = {}

    async def first_event():
        state["release"] = asyncio.Event()

        async def background():
            started.set()
            await state["release"].wait()
            return "completed"

        state["task"] = asyncio.create_task(background())

    thread = threading.Thread(target=lambda: run_awaitable(first_event()))
    thread.start()
    thread.join(5)
    assert not thread.is_alive()
    assert started.wait(2)

    async def second_event():
        assert state["task"].done() is False
        state["release"].set()
        return await state["task"]

    assert run_awaitable(second_event()) == "completed"


def test_handler_exception_does_not_stop_runner_and_self_wait_is_rejected():
    loop_ids = []

    async def failing_event():
        loop_ids.append(id(asyncio.get_running_loop()))
        raise ValueError("event failed")

    with pytest.raises(ValueError, match="event failed"):
        run_awaitable(failing_event())

    async def next_event():
        loop_ids.append(id(asyncio.get_running_loop()))
        with pytest.raises(RuntimeError, match="cannot synchronously wait"):
            run_awaitable(asyncio.sleep(0))
        return "still running"

    assert run_awaitable(next_event()) == "still running"
    assert len(set(loop_ids)) == 1


def test_shutdown_cancels_background_tasks_and_leaves_no_runner_thread():
    cancelled = threading.Event()

    async def create_background_task():
        async def background():
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        asyncio.create_task(background())
        await asyncio.sleep(0)

    run_awaitable(create_background_task())
    runner = get_dispatch_runner()
    thread = runner.thread
    assert thread is not None and thread.is_alive()

    assert shutdown_dispatch_runner(timeout=5)
    assert cancelled.wait(2)
    assert runner.closed is True
    assert runner.loop is None
    assert not thread.is_alive()


def test_runner_thread_can_request_deferred_shutdown_without_deadlock():
    runner = get_dispatch_runner()

    async def event_requests_stop():
        request_dispatch_runner_shutdown()
        await asyncio.sleep(0)
        return "handler completed"

    assert run_awaitable(event_requests_stop()) == "handler completed"
    assert runner.shutdown(timeout=5)
    assert runner.closed is True


@pytest.mark.parametrize(
    "adapter",
    [OneBot, Milky, Feishu, Kritor],
    ids=["onebot", "milky", "feishu", "kritor"],
)
def test_adapter_stop_keeps_runner_available_for_async_cleanup(
    adapter,
    monkeypatch,
):
    closed = []
    monkeypatch.setattr(adapter, "listener_ran", True)
    monkeypatch.setattr(
        adapter,
        "connection",
        SimpleNamespace(close=lambda: closed.append(adapter.__name__)),
        raising=False,
    )
    if adapter is Feishu:
        monkeypatch.setattr(adapter, "active_event_queue", None)
        monkeypatch.setattr(adapter, "event_server", None)
    if adapter in (OneBot, Kritor):
        monkeypatch.setattr(
            adapter,
            "logger",
            SimpleNamespace(log=lambda *args, **kwargs: None),
        )

    loop_id = run_awaitable(_running_loop_id())
    runner = get_dispatch_runner()
    adapter.stop()

    assert adapter.listener_ran is False
    assert runner.closing is False
    assert run_awaitable(_running_loop_id()) == loop_id
    assert shutdown_dispatch_runner(timeout=5)
    assert runner.closed is True


def test_client_aclose_finishes_plugin_cleanup_after_listener_stop(
    tmp_path,
    monkeypatch,
):
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    (plugins / "cleanup.py").write_text(
        """
import asyncio
from jianer.plugins import PluginMetadata

__plugin_meta__ = PluginMetadata(name="jianerbot-plugin-runner-cleanup")

async def shutdown(client, manager):
    client.order.append(("plugin", id(asyncio.get_running_loop())))
""",
        encoding="utf-8",
    )

    runner_loop_id = run_awaitable(_running_loop_id())
    runner = get_dispatch_runner()
    client = Client()
    client.order = []
    manager = PluginManager()
    assert manager.load_plugins(plugins).failed == []
    manager.setup_client(client)
    client.plugin_manager = manager

    monkeypatch.setattr(OneBot, "listener_ran", True)
    monkeypatch.setattr(
        OneBot,
        "connection",
        SimpleNamespace(close=lambda: client.order.append(("stop", None))),
        raising=False,
    )
    monkeypatch.setattr(
        OneBot,
        "logger",
        SimpleNamespace(log=lambda *args, **kwargs: None),
    )
    client.lis = SimpleNamespace(stop=OneBot.stop)

    report = run_awaitable(client.aclose())

    assert report.completed is True
    assert client.order == [
        ("stop", None),
        ("plugin", runner_loop_id),
    ]
    assert runner.closing is False
    assert run_awaitable(_running_loop_id()) == runner_loop_id
    assert shutdown_dispatch_runner(timeout=5)
    assert runner.closed is True


def test_client_sync_close_runs_shutdown_on_the_dispatch_loop(monkeypatch):
    dispatch_loop_id = run_awaitable(
        _running_loop_id()
    )
    runner = get_dispatch_runner()
    client = Client()
    original_aclose = client.aclose
    shutdown_loop_ids = []

    async def recording_aclose():
        shutdown_loop_ids.append(id(asyncio.get_running_loop()))
        return await original_aclose()

    monkeypatch.setattr(client, "aclose", recording_aclose)
    report = client.close()

    assert report.completed is True
    assert shutdown_loop_ids == [dispatch_loop_id]
    assert runner.closed is True
    assert runner.loop is None


async def _running_loop_id():
    return id(asyncio.get_running_loop())

"""Lifecycle and subscription tests for dependency-aware plugins."""

import asyncio
import os
import threading
import textwrap
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from jianer import Client
from jianer.plugins import (
    PluginManager,
    PluginManagerState,
    PluginSetupError,
)


def _write(path: Path, body: str) -> None:
    path.write_text(textwrap.dedent(body), encoding="utf-8")


def _message_plugin(path: Path, plugin_id: str, label: str) -> None:
    _write(
        path,
        f"""
        from jianer.plugins import PluginMetadata

        __plugin_meta__ = PluginMetadata(name={plugin_id!r})

        async def on_message_observe(event, actions):
            actions.calls.append({label + "-observe"!r})

        async def on_message(event, actions):
            actions.calls.append({label + "-normal"!r})
            return True

        async def on_message_fallback(event, actions):
            actions.calls.append({label + "-fallback"!r})
            return True
        """,
    )


def test_dispatch_observers_default_and_explicit_pipeline(tmp_path):
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    _message_plugin(
        plugins / "message.py",
        "jianerbot-plugin-message",
        "plugin",
    )
    manager = PluginManager()
    assert manager.load_plugins(plugins).failed == []

    actions = SimpleNamespace(calls=[])
    assert asyncio.run(manager.dispatch(object(), actions)) is True
    assert actions.calls == ["plugin-observe", "plugin-normal"]

    actions.calls.clear()

    async def explicit_pipeline():
        await manager.observe(object(), actions)
        handled = await manager.dispatch(
            object(), actions, run_observers=False
        )
        fallback = await manager.dispatch_fallback(object(), actions)
        return handled, fallback

    assert asyncio.run(explicit_pipeline()) == (True, True)
    assert actions.calls == [
        "plugin-observe",
        "plugin-normal",
        "plugin-fallback",
    ]
    asyncio.run(manager.shutdown())


def test_subscription_token_is_exact_and_idempotent():
    client = Client()
    calls = []

    async def first(event, actions):
        calls.append("first")

    async def second(event, actions):
        calls.append("second")

    first_token = client.subscribe(first, dict)
    client.subscribe(second, dict)
    assert client.unsubscribe(first_token) is True
    assert client.unsubscribe(first_token) is False

    asyncio.run(client.distributor({}, object()))
    assert calls == ["second"]
    asyncio.run(client.aclose())


def test_staged_setup_subscriptions_activate_and_shutdown_in_reverse_order(
    tmp_path,
):
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    _write(
        plugins / "dependency.py",
        """
        from jianer.plugins import PluginMetadata
        __plugin_meta__ = PluginMetadata(name="jianerbot-plugin-dependency")

        def setup(client, manager):
            async def callback(event, actions):
                actions.calls.append("dependency")
            client.subscribe(callback, dict)

        async def shutdown(client, manager):
            client.shutdown_order.append("dependency")
        """,
    )
    _write(
        plugins / "main.py",
        """
        from jianer.plugins import PluginMetadata
        __plugin_meta__ = PluginMetadata(
            name="jianerbot-plugin-main",
            requires={"jianerbot-plugin-dependency"},
        )

        def setup(client, manager):
            async def callback(event, actions):
                actions.calls.append("main")
            client.subscribe(callback, dict)

        def shutdown(client, manager):
            client.shutdown_order.append("main")
        """,
    )
    client = Client()
    client.shutdown_order = []
    manager = PluginManager()
    assert manager.load_plugins(plugins).failed == []
    manager.setup_client(client, activate=False)
    assert manager.client is client
    assert manager.state == PluginManagerState.STAGED

    actions = SimpleNamespace(calls=[])
    asyncio.run(client.distributor({}, actions))
    assert actions.calls == []

    client.swap_plugin_manager(manager, expected=None)
    asyncio.run(client.distributor({}, actions))
    assert set(actions.calls) == {"dependency", "main"}

    first_report = asyncio.run(manager.shutdown())
    second_report = asyncio.run(manager.shutdown())
    assert first_report.completed is True
    assert second_report == first_report
    assert client.shutdown_order == ["main", "dependency"]
    assert dict not in client.records
    asyncio.run(client.aclose())


def test_setup_failure_rolls_back_owned_subscriptions(tmp_path):
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    _write(
        plugins / "broken.py",
        """
        from jianer.plugins import PluginMetadata
        __plugin_meta__ = PluginMetadata(name="jianerbot-plugin-broken")

        def setup(client, manager):
            async def callback(event, actions):
                actions.calls.append("leaked")
            client.subscribe(callback, dict)
            raise RuntimeError("setup boom")
        """,
    )
    client = Client()
    manager = PluginManager()
    assert manager.load_plugins(plugins).failed == []

    with pytest.raises(PluginSetupError, match="setup boom"):
        manager.setup_client(client, activate=False)

    assert manager.state == PluginManagerState.FAILED
    assert dict not in client.records
    actions = SimpleNamespace(calls=[])
    asyncio.run(client.distributor({}, actions))
    assert actions.calls == []
    assert asyncio.run(manager.shutdown()).completed is True
    asyncio.run(client.aclose())


def test_partial_setup_failure_shuts_down_only_completed_plugins(tmp_path):
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    _write(
        plugins / "dependency.py",
        """
        from jianer.plugins import PluginMetadata
        __plugin_meta__ = PluginMetadata(name="jianerbot-plugin-dependency")

        def setup(client, manager):
            client.order.append("dependency.setup")

        async def shutdown(client, manager):
            client.order.append("dependency.shutdown")
        """,
    )
    _write(
        plugins / "broken.py",
        """
        from jianer.plugins import PluginMetadata
        __plugin_meta__ = PluginMetadata(
            name="jianerbot-plugin-broken",
            requires={"jianerbot-plugin-dependency"},
        )

        def setup(client, manager):
            client.order.append("broken.setup")
            raise RuntimeError("broken setup")

        async def shutdown(client, manager):
            client.order.append("broken.shutdown")
        """,
    )
    client = Client()
    client.order = []
    manager = PluginManager()
    assert manager.load_plugins(plugins).failed == []
    with pytest.raises(PluginSetupError):
        manager.setup_client(client, activate=False)

    assert asyncio.run(manager.shutdown()).completed is True
    assert client.order == [
        "dependency.setup",
        "broken.setup",
        "dependency.shutdown",
    ]
    asyncio.run(client.aclose())


def test_setup_and_shutdown_cannot_resurrect_closed_manager(tmp_path):
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    _write(
        plugins / "blocking.py",
        """
        from jianer.plugins import PluginMetadata
        __plugin_meta__ = PluginMetadata(name="jianerbot-plugin-blocking")

        def setup(client, manager):
            client.setup_started.set()
            assert client.setup_release.wait(5)
        """,
    )
    client = Client()
    client.setup_started = threading.Event()
    client.setup_release = threading.Event()
    manager = PluginManager()
    manager.load_plugins(plugins)
    setup_errors = []
    shutdown_reports = []

    def run_setup():
        try:
            manager.setup_client(client)
        except BaseException as exc:
            setup_errors.append(exc)

    def run_shutdown():
        shutdown_reports.append(asyncio.run(manager.shutdown(timeout=5)))

    setup_thread = threading.Thread(target=run_setup)
    shutdown_thread = threading.Thread(target=run_shutdown)
    setup_thread.start()
    assert client.setup_started.wait(5)
    shutdown_thread.start()
    time.sleep(0.05)
    assert shutdown_thread.is_alive()
    client.setup_release.set()
    setup_thread.join(5)
    shutdown_thread.join(5)

    assert setup_errors == []
    assert shutdown_reports[0].completed is True
    assert manager.state == PluginManagerState.CLOSED
    with pytest.raises(RuntimeError, match="closed"):
        manager.setup_client(client)
    asyncio.run(client.aclose())


def test_atomic_swap_uses_new_manager_and_preserves_old_until_shutdown(tmp_path):
    old_folder = tmp_path / "old"
    new_folder = tmp_path / "new"
    old_folder.mkdir()
    new_folder.mkdir()
    _message_plugin(
        old_folder / "message.py",
        "jianerbot-plugin-message",
        "old",
    )
    _message_plugin(
        new_folder / "message.py",
        "jianerbot-plugin-message",
        "new",
    )
    client = Client()
    old_manager = PluginManager()
    new_manager = PluginManager()
    assert old_manager.load_plugins(old_folder).failed == []
    assert new_manager.load_plugins(new_folder).failed == []
    old_manager.setup_client(client)
    client.plugin_manager = old_manager
    new_manager.setup_client(client, activate=False)

    replaced = client.swap_plugin_manager(new_manager, expected=old_manager)
    assert replaced is old_manager
    assert old_manager.state == PluginManagerState.DRAINING
    assert new_manager.state == PluginManagerState.ACTIVE

    actions = SimpleNamespace(calls=[])
    assert asyncio.run(client._dispatch_plugin_message(object(), actions)) is True
    assert actions.calls == ["new-observe", "new-normal"]
    assert asyncio.run(old_manager.shutdown()).completed is True
    asyncio.run(client.aclose())


def test_failed_new_activation_keeps_old_manager_active(tmp_path, monkeypatch):
    old_folder = tmp_path / "old"
    new_folder = tmp_path / "new"
    old_folder.mkdir()
    new_folder.mkdir()
    _message_plugin(
        old_folder / "message.py",
        "jianerbot-plugin-message",
        "old",
    )
    _message_plugin(
        new_folder / "message.py",
        "jianerbot-plugin-message",
        "new",
    )
    client = Client()
    old_manager = PluginManager()
    new_manager = PluginManager()
    old_manager.load_plugins(old_folder)
    new_manager.load_plugins(new_folder)
    old_manager.setup_client(client)
    client.plugin_manager = old_manager
    new_manager.setup_client(client, activate=False)

    def fail_activation():
        raise RuntimeError("activation failed")

    monkeypatch.setattr(
        new_manager,
        "_activate_for_swap_locked",
        fail_activation,
    )
    with pytest.raises(RuntimeError, match="activation failed"):
        client.swap_plugin_manager(new_manager, expected=old_manager)

    assert client.plugin_manager is old_manager
    assert old_manager.state == PluginManagerState.ACTIVE
    actions = SimpleNamespace(calls=[])
    assert asyncio.run(
        client._dispatch_plugin_message(object(), actions)
    ) is True
    assert actions.calls == ["old-observe", "old-normal"]
    asyncio.run(new_manager.shutdown())
    asyncio.run(client.aclose())


def test_swap_rejects_manager_that_is_not_staged(tmp_path):
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    _message_plugin(
        plugins / "message.py",
        "jianerbot-plugin-message",
        "new",
    )
    client = Client()
    manager = PluginManager()
    manager.load_plugins(plugins)
    manager.client = client
    with pytest.raises(RuntimeError, match="staged"):
        client.swap_plugin_manager(manager, expected=None)
    assert client.plugin_manager is None
    asyncio.run(manager.shutdown())
    asyncio.run(client.aclose())


def test_staged_incremental_plugin_must_finish_setup_before_swap(tmp_path):
    old_folder = tmp_path / "old"
    staged_folder = tmp_path / "staged"
    extra_folder = tmp_path / "extra"
    old_folder.mkdir()
    staged_folder.mkdir()
    extra_folder.mkdir()
    _message_plugin(
        old_folder / "message.py",
        "jianerbot-plugin-message",
        "old",
    )
    extra = extra_folder / "extra.py"
    _write(
        extra,
        """
        from jianer.plugins import PluginMetadata
        __plugin_meta__ = PluginMetadata(name="jianerbot-plugin-extra")
        def setup(client, manager):
            client.extra_setup = True
        """,
    )
    client = Client()
    client.extra_setup = False
    old_manager = PluginManager()
    old_manager.load_plugins(old_folder)
    old_manager.setup_client(client)
    client.plugin_manager = old_manager

    staged = PluginManager()
    staged.load_plugins(staged_folder)
    staged.setup_client(client, activate=False)
    assert staged.load_plugin(extra) is not None
    with pytest.raises(RuntimeError, match="pending setup"):
        client.swap_plugin_manager(staged, expected=old_manager)
    assert client.plugin_manager is old_manager
    assert client.extra_setup is False

    staged.setup_client(client, activate=False)
    assert client.extra_setup is True
    replaced = client.swap_plugin_manager(staged, expected=old_manager)
    assert replaced is old_manager
    asyncio.run(old_manager.shutdown())
    asyncio.run(client.aclose())


def test_client_swap_cannot_race_between_manager_selection_and_lease(tmp_path):
    old_folder = tmp_path / "old"
    new_folder = tmp_path / "new"
    old_folder.mkdir()
    new_folder.mkdir()
    _message_plugin(
        old_folder / "message.py",
        "jianerbot-plugin-message",
        "old",
    )
    _message_plugin(
        new_folder / "message.py",
        "jianerbot-plugin-message",
        "new",
    )
    client = Client()
    old_manager = PluginManager()
    new_manager = PluginManager()
    old_manager.load_plugins(old_folder)
    new_manager.load_plugins(new_folder)
    old_manager.setup_client(client)
    client.plugin_manager = old_manager
    new_manager.setup_client(client, activate=False)

    acquire_entered = threading.Event()
    allow_acquire = threading.Event()
    swap_finished = threading.Event()
    dispatch_result = {}
    actions = SimpleNamespace(calls=[])
    original_acquire = old_manager._acquire_dispatch

    def delayed_acquire():
        acquire_entered.set()
        assert allow_acquire.wait(5)
        return original_acquire()

    old_manager._acquire_dispatch = delayed_acquire

    def dispatch_in_thread():
        dispatch_result["handled"] = asyncio.run(
            client._dispatch_plugin_message(object(), actions)
        )

    def swap_in_thread():
        client.swap_plugin_manager(new_manager, expected=old_manager)
        swap_finished.set()

    dispatch_thread = threading.Thread(target=dispatch_in_thread)
    swap_thread = threading.Thread(target=swap_in_thread)
    dispatch_thread.start()
    assert acquire_entered.wait(5)
    swap_thread.start()
    time.sleep(0.05)
    assert swap_finished.is_set() is False
    allow_acquire.set()
    dispatch_thread.join(5)
    swap_thread.join(5)

    assert dispatch_result == {"handled": True}
    assert actions.calls == ["old-observe", "old-normal"]
    assert swap_finished.is_set() is True
    asyncio.run(old_manager.shutdown())
    asyncio.run(client.aclose())


def test_swap_and_shutdown_lock_order_cannot_deadlock(tmp_path):
    old_folder = tmp_path / "old"
    new_folder = tmp_path / "new"
    old_folder.mkdir()
    new_folder.mkdir()
    _message_plugin(
        old_folder / "message.py",
        "jianerbot-plugin-message",
        "old",
    )
    _message_plugin(
        new_folder / "message.py",
        "jianerbot-plugin-message",
        "new",
    )
    client = Client()
    old_manager = PluginManager()
    new_manager = PluginManager()
    old_manager.load_plugins(old_folder)
    new_manager.load_plugins(new_folder)
    old_manager.setup_client(client)
    client.plugin_manager = old_manager
    new_manager.setup_client(client, activate=False)
    shutdown_reports = []
    swap_errors = []

    def shutdown_old():
        shutdown_reports.append(asyncio.run(old_manager.shutdown(timeout=5)))

    def attempt_swap():
        try:
            client.swap_plugin_manager(new_manager, expected=old_manager)
        except BaseException as exc:
            swap_errors.append(exc)

    client._subscription_lock.acquire()
    try:
        shutdown_thread = threading.Thread(target=shutdown_old)
        shutdown_thread.start()
        deadline = time.monotonic() + 5
        while (
            old_manager.state != PluginManagerState.DRAINING
            and time.monotonic() < deadline
        ):
            time.sleep(0.005)
        assert old_manager.state == PluginManagerState.DRAINING
        swap_thread = threading.Thread(target=attempt_swap)
        swap_thread.start()
        time.sleep(0.05)
        assert shutdown_thread.is_alive()
        assert swap_thread.is_alive()
    finally:
        client._subscription_lock.release()

    shutdown_thread.join(5)
    swap_thread.join(5)
    assert shutdown_thread.is_alive() is False
    assert swap_thread.is_alive() is False
    assert shutdown_reports[0].completed is True
    assert len(swap_errors) == 1
    assert "no longer active" in str(swap_errors[0])
    assert client.plugin_manager is old_manager
    asyncio.run(new_manager.shutdown())
    asyncio.run(client.aclose())


def test_shutdown_waits_for_inflight_dispatch(tmp_path):
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    _write(
        plugins / "slow.py",
        """
        from jianer.plugins import PluginMetadata
        __plugin_meta__ = PluginMetadata(name="jianerbot-plugin-slow")

        async def on_message(event, actions):
            actions.started.set()
            await actions.release.wait()
            actions.calls.append("finished")
            return True
        """,
    )
    manager = PluginManager()
    assert manager.load_plugins(plugins).failed == []
    manager.activate()

    async def scenario():
        actions = SimpleNamespace(
            started=asyncio.Event(),
            release=asyncio.Event(),
            calls=[],
        )
        dispatch_task = asyncio.create_task(manager.dispatch(object(), actions))
        await actions.started.wait()
        shutdown_task = asyncio.create_task(manager.shutdown(timeout=5))
        await asyncio.sleep(0)
        assert shutdown_task.done() is False
        actions.release.set()
        assert await dispatch_task is True
        report = await shutdown_task
        return actions.calls, report

    calls, report = asyncio.run(scenario())
    assert calls == ["finished"]
    assert report.completed is True


def test_shutdown_drain_timeout_can_be_retried(tmp_path):
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    _write(
        plugins / "slow.py",
        """
        from jianer.plugins import PluginMetadata
        __plugin_meta__ = PluginMetadata(name="jianerbot-plugin-slow")

        async def on_message(event, actions):
            actions.started.set()
            await actions.release.wait()
            return True
        """,
    )
    manager = PluginManager()
    manager.load_plugins(plugins)
    manager.activate()

    async def scenario():
        actions = SimpleNamespace(
            started=asyncio.Event(),
            release=asyncio.Event(),
        )
        dispatch_task = asyncio.create_task(manager.dispatch(object(), actions))
        await actions.started.wait()
        first = await manager.shutdown(timeout=0.01)
        assert first.completed is False
        assert manager.state == PluginManagerState.DRAINING
        actions.release.set()
        assert await dispatch_task is True
        second = await manager.shutdown(timeout=5)
        return first, second

    first, second = asyncio.run(scenario())
    assert "timed out" in first.errors[0]
    assert second.completed is True
    assert manager.state == PluginManagerState.CLOSED


def test_cancelled_manager_shutdown_releases_singleflight_for_retry(tmp_path):
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    _write(
        plugins / "closing.py",
        """
        from jianer.plugins import PluginMetadata
        __plugin_meta__ = PluginMetadata(name="jianerbot-plugin-closing")

        async def shutdown(client, manager):
            client.shutdown_started.set()
            await client.shutdown_release.wait()
        """,
    )
    client = Client()
    client.shutdown_started = asyncio.Event()
    client.shutdown_release = asyncio.Event()
    manager = PluginManager()
    manager.load_plugins(plugins)
    manager.setup_client(client)

    async def scenario():
        first = asyncio.create_task(manager.shutdown())
        await client.shutdown_started.wait()
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        assert manager._shutdown_started is False
        assert manager._shutdown_finished.is_set()
        client.shutdown_release.set()
        return await manager.shutdown()

    report = asyncio.run(scenario())
    assert report.completed is True
    assert manager.state == PluginManagerState.CLOSED
    asyncio.run(client.aclose())


def test_shutdown_waits_for_inflight_setup_subscription(tmp_path):
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    _write(
        plugins / "subscriber.py",
        """
        from jianer.plugins import PluginMetadata
        __plugin_meta__ = PluginMetadata(name="jianerbot-plugin-subscriber")

        def setup(client, manager):
            async def callback(event, actions):
                actions.started.set()
                await actions.release.wait()
                actions.calls.append("finished")
            client.subscribe(callback, dict)
        """,
    )
    client = Client()
    manager = PluginManager()
    manager.load_plugins(plugins)
    manager.setup_client(client)
    client.plugin_manager = manager

    async def scenario():
        actions = SimpleNamespace(
            started=asyncio.Event(),
            release=asyncio.Event(),
            calls=[],
        )
        callback_task = asyncio.create_task(client.distributor({}, actions))
        await actions.started.wait()
        shutdown_task = asyncio.create_task(manager.shutdown(timeout=5))
        await asyncio.sleep(0)
        assert shutdown_task.done() is False
        actions.release.set()
        await callback_task
        report = await shutdown_task
        return actions.calls, report

    calls, report = asyncio.run(scenario())
    assert calls == ["finished"]
    assert report.completed is True
    asyncio.run(client.aclose())


def test_client_context_exit_and_restart_close_plugins_first(tmp_path, monkeypatch):
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    _write(
        plugins / "closing.py",
        """
        from jianer.plugins import PluginMetadata
        __plugin_meta__ = PluginMetadata(name="jianerbot-plugin-closing")

        async def shutdown(client, manager):
            client.order.append("shutdown")
        """,
    )
    client = Client()
    client.order = []
    client.lis = SimpleNamespace(stop=lambda: client.order.append("stop"))
    manager = PluginManager()
    assert manager.load_plugins(plugins).failed == []
    manager.setup_client(client)
    client.plugin_manager = manager
    monkeypatch.setattr(
        os,
        "execv",
        lambda executable, argv: client.order.append("exec"),
    )

    asyncio.run(client.arestart())
    assert client.order == ["stop", "shutdown", "exec"]

    sync_client = Client()
    with sync_client:
        pass
    assert sync_client._closed is True

    async def async_context():
        async_client = Client()
        async with async_client:
            pass
        return async_client._closed

    assert asyncio.run(async_context()) is True


def test_sync_restart_inside_event_loop_hands_off_shutdown(
    tmp_path,
    monkeypatch,
):
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    _write(
        plugins / "closing.py",
        """
        from jianer.plugins import PluginMetadata
        __plugin_meta__ = PluginMetadata(name="jianerbot-plugin-closing")

        async def shutdown(client, manager):
            client.order.append("shutdown")
        """,
    )
    client = Client()
    client.order = []
    client.lis = SimpleNamespace(stop=lambda: client.order.append("stop"))
    manager = PluginManager()
    manager.load_plugins(plugins)
    manager.setup_client(client)
    client.plugin_manager = manager
    monkeypatch.setattr(
        os,
        "execv",
        lambda executable, argv: client.order.append("exec"),
    )

    async def scenario():
        client.restart()
        assert client._restart_task is not None
        await client._restart_task

    asyncio.run(scenario())
    assert client.order == ["stop", "shutdown", "exec"]


def test_cancelled_client_close_and_manager_timeout_are_retryable(
    tmp_path,
    monkeypatch,
):
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    _write(
        plugins / "slow.py",
        """
        from jianer.plugins import PluginMetadata
        __plugin_meta__ = PluginMetadata(name="jianerbot-plugin-slow")

        async def on_message(event, actions):
            actions.started.set()
            await actions.release.wait()
            return True

        async def shutdown(client, manager):
            client.shutdown_started.set()
            await client.shutdown_release.wait()
        """,
    )

    async def cancellation_scenario():
        client = Client()
        client.shutdown_started = asyncio.Event()
        client.shutdown_release = asyncio.Event()
        manager = PluginManager()
        manager.load_plugins(plugins)
        manager.setup_client(client)
        client.plugin_manager = manager
        first = asyncio.create_task(client.aclose())
        await client.shutdown_started.wait()
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        assert client._close_started is False
        assert client._close_finished.is_set()
        assert client._closed is False
        client.shutdown_release.set()
        report = await client.aclose()
        return client, report

    cancelled_client, cancelled_report = asyncio.run(cancellation_scenario())
    assert cancelled_report.completed is True
    assert cancelled_client._closed is True

    async def timeout_scenario():
        client = Client()
        client.shutdown_started = asyncio.Event()
        client.shutdown_release = asyncio.Event()
        client.shutdown_release.set()
        manager = PluginManager()
        manager.load_plugins(plugins)
        manager.setup_client(client)
        client.plugin_manager = manager
        original_shutdown = manager.shutdown

        async def short_shutdown(*, timeout=30.0):
            return await original_shutdown(timeout=0.01)

        monkeypatch.setattr(manager, "shutdown", short_shutdown)
        actions = SimpleNamespace(
            started=asyncio.Event(),
            release=asyncio.Event(),
        )
        dispatch = asyncio.create_task(manager.dispatch(object(), actions))
        await actions.started.wait()
        first = await client.aclose()
        assert first.completed is False
        assert client._closed is False
        assert client._close_report is None
        actions.release.set()
        assert await dispatch is True
        second = await client.aclose()
        return client, first, second

    timeout_client, first_report, second_report = asyncio.run(timeout_scenario())
    assert "timed out" in first_report.errors[0]
    assert second_report.completed is True
    assert timeout_client._closed is True


def test_load_rejected_after_activation_failure_draining_or_close(tmp_path):
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    _message_plugin(
        plugins / "message.py",
        "jianerbot-plugin-message",
        "plugin",
    )

    active = PluginManager()
    active.load_plugins(plugins)
    active.activate()
    with pytest.raises(RuntimeError, match="active"):
        active.load_plugin(plugins / "message.py")
    asyncio.run(active.shutdown())

    closed = PluginManager()
    asyncio.run(closed.shutdown())
    missing = tmp_path / "must-not-be-created"
    with pytest.raises(RuntimeError, match="closed"):
        closed.load_plugins(missing)
    assert missing.exists() is False

    client = Client()
    failed_plugins = tmp_path / "failed"
    failed_plugins.mkdir()
    _write(
        failed_plugins / "broken.py",
        """
        from jianer.plugins import PluginMetadata
        __plugin_meta__ = PluginMetadata(name="jianerbot-plugin-broken")
        def setup(client, manager):
            raise RuntimeError("boom")
        """,
    )
    failed = PluginManager()
    failed.load_plugins(failed_plugins)
    with pytest.raises(PluginSetupError):
        failed.setup_client(client)
    with pytest.raises(RuntimeError, match="failed"):
        failed.load_plugin(failed_plugins / "broken.py")
    asyncio.run(failed.shutdown())
    asyncio.run(client.aclose())

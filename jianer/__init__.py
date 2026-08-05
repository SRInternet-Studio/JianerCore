from . import configurator
from .utils import screens

import asyncio
import contextlib
import inspect
import os
import sys
import threading
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Union

from .asyncio_runner import (
    AsyncioRunner,
    get_dispatch_runner,
    request_dispatch_runner_shutdown,
    run_awaitable,
    shutdown_dispatch_runner,
    submit_awaitable,
)
from .plugins.runtime import (
    PluginManagerState,
    ShutdownReport,
    SubscriptionOwner,
    SubscriptionToken,
    current_plugin_owner,
)

JIANER_BOT_VERSION = "0.92.3"

# listener = None

screens.play_startup()
screens.play_info(JIANER_BOT_VERSION)


@dataclass
class _SubscriptionRecord:
    token: SubscriptionToken
    func: Callable[..., Any]
    event_type: type
    owner: SubscriptionOwner | None


class Client:
    def __init__(self):
        self.records = {}
        self.lis = None
        self.plugin_manager = None
        self._plugin_dispatch_registered = False
        self._subscriptions = {}
        self._event_tokens = {}
        self._owner_enabled = {}
        self._owner_inflight = {}
        self._owner_drained = {}
        self._subscription_lock = threading.RLock()
        self._lifecycle_lock = threading.RLock()
        self._close_lock = threading.Lock()
        self._close_started = False
        self._close_finished = threading.Event()
        self._close_report = None
        self._closed = False
        self._restart_task = None

    def subscribe(
        self,
        func: Callable[..., Any],
        event: Union[
            "events.GroupMessageEvent",
            "events.PrivateMessageEvent",
            "events.GroupFileUploadEvent",
            "events.GroupAdminEvent",
            "events.GroupMemberDecreaseEvent",
            "events.GroupMemberIncreaseEvent",
            "events.GroupMuteEvent",
            "events.FriendAddEvent",
            "events.GroupRecallEvent",
            "events.FriendRecallEvent",
            "events.NotifyEvent",
            "events.GroupEssenceEvent",
            "events.MessageReactionEvent",
            "events.BotMenuEvent",
            "events.GroupAddInviteEvent",
            "events.HyperListenerStartNotify",
            "events.HyperListenerStopNotify",
        ],
        *,
        owner: SubscriptionOwner | None = None,
    ) -> SubscriptionToken:
        if owner is None:
            owner = current_plugin_owner()
        token = SubscriptionToken(uuid.uuid4().hex, event)
        record = _SubscriptionRecord(token, func, event, owner)
        with self._subscription_lock:
            if self._closed:
                raise RuntimeError("cannot subscribe to a closed Client")
            self._subscriptions[token.token] = record
            self._event_tokens.setdefault(event, []).append(token.token)
            self.records.setdefault(event, []).append(func)
            if owner is not None:
                self._owner_enabled.setdefault(owner.manager_id, False)
                drained = self._owner_drained.setdefault(
                    owner.manager_id, threading.Event()
                )
                if self._owner_inflight.get(owner.manager_id, 0) == 0:
                    drained.set()
        return token

    def unsubscribe(self, token: SubscriptionToken) -> bool:
        """Remove exactly one subscription returned by :meth:`subscribe`."""

        with self._subscription_lock:
            record = self._subscriptions.pop(token.token, None)
            if record is None:
                return False
            event_tokens = self._event_tokens.get(record.event_type, [])
            try:
                event_tokens.remove(token.token)
            except ValueError:
                pass
            if event_tokens:
                self._event_tokens[record.event_type] = event_tokens
                self.records[record.event_type] = [
                    self._subscriptions[item].func
                    for item in event_tokens
                    if item in self._subscriptions
                ]
            else:
                self._event_tokens.pop(record.event_type, None)
                self.records.pop(record.event_type, None)
            return True

    async def distributor(
        self,
        message_data: Union["events.Event", "events.HyperNotify"],
        actions: "Listener.Actions",
    ) -> None:
        with self._subscription_lock:
            token_ids = list(self._event_tokens.get(type(message_data), ()))
        if not token_ids:
            return
        tasks = []
        for token_id in token_ids:
            record = self._begin_subscription(token_id)
            if record is None:
                continue
            tasks.append(
                asyncio.create_task(
                    self._invoke_subscription(record, message_data, actions)
                )
            )
        if tasks:
            await asyncio.gather(*tasks)

    def load_plugins(
        self,
        *plugin_folders,
        auto_dispatch: bool = True,
        **kwargs,
    ):
        from . import events
        from .plugins import PluginManager

        if self.plugin_manager is None:
            self.plugin_manager = PluginManager()
        result = self.plugin_manager.load_plugins(*plugin_folders, **kwargs)
        if auto_dispatch and not self._plugin_dispatch_registered:
            self.subscribe(self._dispatch_plugin_message, events.GroupMessageEvent)
            self.subscribe(self._dispatch_plugin_message, events.PrivateMessageEvent)
            self._plugin_dispatch_registered = True
        self.plugin_manager.setup_client(self)
        return result

    def swap_plugin_manager(self, new_manager, *, expected):
        """Atomically activate a staged manager and retire the expected manager."""

        if new_manager.client is not self:
            raise RuntimeError("new PluginManager must be staged against this Client")
        managers = [new_manager]
        if expected is not None and expected is not new_manager:
            managers.append(expected)
        managers.sort(key=lambda manager: manager.manager_id)
        with contextlib.ExitStack() as stack:
            for manager in managers:
                stack.enter_context(manager._transition_lock)
            with self._lifecycle_lock, self._subscription_lock:
                if self.plugin_manager is not expected:
                    raise RuntimeError(
                        "active PluginManager changed during staged reload"
                    )
                if (
                    expected is not None
                    and expected.state != PluginManagerState.ACTIVE
                ):
                    raise RuntimeError(
                        "expected PluginManager is no longer active"
                    )
                new_manager._activate_for_swap_locked()
                old_manager = self.plugin_manager
                if old_manager is not None:
                    old_manager._begin_draining_for_swap_locked()
                    self._owner_enabled[old_manager.manager_id] = False
                self._owner_enabled[new_manager.manager_id] = True
                self.plugin_manager = new_manager
                return old_manager

    def run(self):
        from . import listener

        self.lis = listener
        self.lis.reg(self.distributor)
        try:
            if self.records:
                self.lis.run()
        finally:
            try:
                if not self._closed:
                    self.close()
            finally:
                shutdown_dispatch_runner()

    def restart(self) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self.close()
            if not self._closed:
                raise RuntimeError("Client shutdown did not complete; restart aborted")
            os.execv(sys.executable, [sys.executable] + sys.argv)
            return
        if self._restart_task is None:
            self._restart_task = loop.create_task(self.arestart())

    async def arestart(self) -> None:
        await self.aclose()
        if not self._closed:
            raise RuntimeError("Client shutdown did not complete; restart aborted")
        os.execv(sys.executable, [sys.executable] + sys.argv)

    async def aclose(self) -> ShutdownReport:
        with self._close_lock:
            if self._close_report is not None:
                return self._close_report
            if self._close_started:
                wait_for_other = True
            else:
                self._close_started = True
                self._close_finished.clear()
                wait_for_other = False

        if wait_for_other:
            await asyncio.to_thread(self._close_finished.wait)
            with self._close_lock:
                report = self._close_report
            if report is not None:
                return report
            return await self.aclose()

        try:
            errors = []
            if self.lis is not None:
                try:
                    self.lis.stop()
                except Exception as exc:
                    errors.append(f"listener stop failed: {exc}")

            while True:
                with self._lifecycle_lock:
                    manager = self.plugin_manager
                if manager is None:
                    break
                with manager._transition_lock:
                    with self._lifecycle_lock:
                        if self.plugin_manager is not manager:
                            continue
                        manager._begin_draining()
                        break

            manager_report = None
            if manager is not None:
                manager_report = await manager.shutdown()
                errors.extend(manager_report.errors)

            manager_finished = (
                manager is None
                or manager.state == PluginManagerState.CLOSED
            )
            report = ShutdownReport(
                manager_id=(
                    manager_report.manager_id
                    if manager_report is not None
                    else "client"
                ),
                completed=manager_finished and not errors,
                errors=tuple(errors),
            )
            if not manager_finished:
                self._reset_close_singleflight()
                return report

            with self._subscription_lock:
                self._subscriptions.clear()
                self._event_tokens.clear()
                self.records.clear()
                self._owner_enabled.clear()
                self._closed = True

            with self._close_lock:
                self._close_report = report
                self._close_finished.set()
            return report
        except BaseException:
            self._reset_close_singleflight()
            raise

    def close(self) -> ShutdownReport:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            try:
                return run_awaitable(self.aclose())
            finally:
                shutdown_dispatch_runner()
        raise RuntimeError(
            "Client.close() cannot run inside an event loop; "
            "use 'await client.aclose()'"
        )

    def _reset_close_singleflight(self) -> None:
        with self._close_lock:
            if self._close_report is None:
                self._close_started = False
            self._close_finished.set()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.aclose()

    async def _dispatch_plugin_message(self, event: Any, actions: Any) -> bool:
        with self._lifecycle_lock:
            manager = self.plugin_manager
            if manager is None or not manager._acquire_dispatch():
                return False
        try:
            return await manager._dispatch_with_acquired_lease(event, actions)
        finally:
            manager._release_dispatch()

    def _begin_subscription(self, token_id: str) -> _SubscriptionRecord | None:
        with self._subscription_lock:
            record = self._subscriptions.get(token_id)
            if record is None:
                return None
            owner = record.owner
            if owner is not None:
                if not self._owner_enabled.get(owner.manager_id, False):
                    return None
                self._owner_inflight[owner.manager_id] = (
                    self._owner_inflight.get(owner.manager_id, 0) + 1
                )
                self._owner_drained.setdefault(
                    owner.manager_id, threading.Event()
                ).clear()
            return record

    async def _invoke_subscription(
        self,
        record: _SubscriptionRecord,
        event: Any,
        actions: Any,
    ) -> Any:
        try:
            response = record.func(event, actions)
            if inspect.isawaitable(response):
                response = await response
            return response
        finally:
            owner = record.owner
            if owner is not None:
                with self._subscription_lock:
                    remaining = max(
                        0,
                        self._owner_inflight.get(owner.manager_id, 0) - 1,
                    )
                    self._owner_inflight[owner.manager_id] = remaining
                    if remaining == 0:
                        self._owner_drained.setdefault(
                            owner.manager_id, threading.Event()
                        ).set()

    def _set_plugin_owner_enabled(self, manager_id: str, enabled: bool) -> None:
        with self._subscription_lock:
            self._owner_enabled[manager_id] = bool(enabled)
            drained = self._owner_drained.setdefault(
                manager_id, threading.Event()
            )
            if self._owner_inflight.get(manager_id, 0) == 0:
                drained.set()

    def _wait_plugin_owner_drained(
        self,
        manager_id: str,
        timeout: float | None = None,
    ) -> bool:
        with self._subscription_lock:
            drained = self._owner_drained.setdefault(
                manager_id, threading.Event()
            )
            if self._owner_inflight.get(manager_id, 0) == 0:
                drained.set()
        return drained.wait(timeout)

    def _remove_plugin_owner_subscriptions(self, manager_id: str) -> int:
        with self._subscription_lock:
            tokens = [
                record.token
                for record in self._subscriptions.values()
                if record.owner is not None
                and record.owner.manager_id == manager_id
            ]
        removed = 0
        for token in tokens:
            if self.unsubscribe(token):
                removed += 1
        with self._subscription_lock:
            self._owner_enabled.pop(manager_id, None)
            self._owner_inflight.pop(manager_id, None)
            self._owner_drained.pop(manager_id, None)
        return removed

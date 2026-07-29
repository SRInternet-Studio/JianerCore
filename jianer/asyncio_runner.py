"""Process-wide asyncio runner used by synchronous protocol listeners.

The protocol adapters receive events on synchronous worker threads.  Keeping one
dedicated loop alive for the lifetime of the listener lets plugins safely retain
asyncio locks, queues, clients, and background tasks between those events.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import inspect
import threading
from typing import Any, Awaitable, Coroutine, Optional, TypeVar


T = TypeVar("T")


class AsyncioRunner:
    """Run awaitables on one reusable event loop owned by a daemon thread."""

    def __init__(self, *, thread_name: str = "jianer-asyncio-runner") -> None:
        self.thread_name = thread_name
        self._state_lock = threading.RLock()
        self._ready = threading.Event()
        self._stopped = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._startup_error: Optional[BaseException] = None
        self._closing = False
        self._closed = False

    @property
    def loop(self) -> Optional[asyncio.AbstractEventLoop]:
        with self._state_lock:
            return self._loop

    @property
    def thread(self) -> Optional[threading.Thread]:
        with self._state_lock:
            return self._thread

    @property
    def closing(self) -> bool:
        with self._state_lock:
            return self._closing

    @property
    def closed(self) -> bool:
        with self._state_lock:
            return self._closed

    def is_runner_thread(self) -> bool:
        with self._state_lock:
            thread = self._thread
        return thread is not None and threading.current_thread() is thread

    def start(self) -> asyncio.AbstractEventLoop:
        """Start the runner if necessary and return its event loop."""

        with self._state_lock:
            if self._closing or self._closed:
                raise RuntimeError("AsyncioRunner is closing or closed")
            if self._loop is not None and self._thread is not None:
                return self._loop
            if self._thread is None:
                self._ready.clear()
                self._stopped.clear()
                self._startup_error = None
                self._thread = threading.Thread(
                    target=self._thread_main,
                    name=self.thread_name,
                    daemon=True,
                )
                self._thread.start()

        self._ready.wait()
        with self._state_lock:
            if self._startup_error is not None:
                raise RuntimeError("AsyncioRunner failed to start") from self._startup_error
            if self._loop is None:
                raise RuntimeError("AsyncioRunner stopped during startup")
            return self._loop

    def submit(self, awaitable: Awaitable[T]) -> concurrent.futures.Future[T]:
        """Submit an awaitable without blocking the calling thread."""

        if self.is_runner_thread():
            self._dispose_awaitable(awaitable)
            raise RuntimeError(
                "AsyncioRunner.submit() cannot be called from its own thread; "
                "use 'await' or asyncio.create_task() there"
            )
        coroutine = self._as_coroutine(awaitable)
        try:
            loop = self.start()
            return asyncio.run_coroutine_threadsafe(coroutine, loop)
        except BaseException:
            coroutine.close()
            raise

    def run(self, awaitable: Awaitable[T], *, timeout: float | None = None) -> T:
        """Synchronously run an awaitable on the persistent event loop."""

        if self.is_runner_thread():
            self._dispose_awaitable(awaitable)
            raise RuntimeError(
                "AsyncioRunner.run() cannot synchronously wait on its own thread; "
                "use 'await' instead"
            )
        return self.submit(awaitable).result(timeout=timeout)

    def request_shutdown(self) -> None:
        """Request loop shutdown without waiting.

        When called by a coroutine already running on this runner, shutdown is
        deferred until that coroutine has returned so its synchronous submitter
        can receive the result.
        """

        with self._state_lock:
            if self._closed or self._closing:
                return
            self._closing = True
            loop = self._loop
            thread = self._thread
        if loop is None or thread is None:
            with self._state_lock:
                self._closed = True
            self._stopped.set()
            return
        if threading.current_thread() is thread:
            current_task = asyncio.current_task(loop=loop)
            if current_task is None:
                loop.call_soon(loop.stop)
            else:
                current_task.add_done_callback(lambda _task: loop.stop())
            return
        loop.call_soon_threadsafe(loop.stop)

    def shutdown(self, *, timeout: float | None = 10.0) -> bool:
        """Stop the runner, cancel remaining tasks, and join its thread."""

        if self.is_runner_thread():
            raise RuntimeError(
                "AsyncioRunner.shutdown() cannot wait on its own thread; "
                "call request_shutdown() instead"
            )
        self.request_shutdown()
        if not self._stopped.wait(timeout):
            return False
        with self._state_lock:
            thread = self._thread
        if thread is not None:
            thread.join(0)
        return True

    def _thread_main(self) -> None:
        loop: Optional[asyncio.AbstractEventLoop] = None
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            with self._state_lock:
                self._loop = loop
            self._ready.set()
            loop.run_forever()
        except BaseException as exc:
            with self._state_lock:
                self._startup_error = exc
            self._ready.set()
        finally:
            if loop is not None:
                self._cancel_remaining_tasks(loop)
                try:
                    loop.run_until_complete(loop.shutdown_asyncgens())
                except BaseException:
                    pass
                shutdown_executor = getattr(loop, "shutdown_default_executor", None)
                if shutdown_executor is not None:
                    try:
                        loop.run_until_complete(shutdown_executor())
                    except BaseException:
                        pass
                asyncio.set_event_loop(None)
                loop.close()
            with self._state_lock:
                self._loop = None
                self._closing = True
                self._closed = True
            self._stopped.set()

    @staticmethod
    def _cancel_remaining_tasks(loop: asyncio.AbstractEventLoop) -> None:
        tasks = [task for task in asyncio.all_tasks(loop) if not task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            loop.run_until_complete(asyncio.gather(*tasks, return_exceptions=True))

    @staticmethod
    async def _await_result(awaitable: Awaitable[T]) -> T:
        return await awaitable

    @classmethod
    def _as_coroutine(cls, awaitable: Awaitable[T]) -> Coroutine[Any, Any, T]:
        if inspect.iscoroutine(awaitable):
            return awaitable
        if not inspect.isawaitable(awaitable):
            raise TypeError("expected an awaitable")
        return cls._await_result(awaitable)

    @staticmethod
    def _dispose_awaitable(awaitable: Awaitable[Any]) -> None:
        close = getattr(awaitable, "close", None)
        if callable(close):
            close()


_DISPATCH_RUNNER_LOCK = threading.RLock()
_DISPATCH_RUNNER: Optional[AsyncioRunner] = None


def get_dispatch_runner() -> AsyncioRunner:
    """Return the process-wide adapter dispatch runner."""

    global _DISPATCH_RUNNER
    with _DISPATCH_RUNNER_LOCK:
        if _DISPATCH_RUNNER is None or _DISPATCH_RUNNER.closed:
            _DISPATCH_RUNNER = AsyncioRunner()
        if _DISPATCH_RUNNER.closing:
            raise RuntimeError("the Jianer asyncio dispatch runner is shutting down")
        return _DISPATCH_RUNNER


def submit_awaitable(awaitable: Awaitable[T]) -> concurrent.futures.Future[T]:
    """Submit an awaitable to the process-wide dispatch runner."""

    try:
        runner = get_dispatch_runner()
    except BaseException:
        AsyncioRunner._dispose_awaitable(awaitable)
        raise
    return runner.submit(awaitable)


def run_awaitable(awaitable: Awaitable[T], *, timeout: float | None = None) -> T:
    """Run an awaitable from a synchronous thread on the shared runner."""

    try:
        runner = get_dispatch_runner()
    except BaseException:
        AsyncioRunner._dispose_awaitable(awaitable)
        raise
    return runner.run(awaitable, timeout=timeout)


def request_dispatch_runner_shutdown() -> None:
    """Request shutdown without blocking the caller."""

    with _DISPATCH_RUNNER_LOCK:
        runner = _DISPATCH_RUNNER
    if runner is not None:
        runner.request_shutdown()


def shutdown_dispatch_runner(*, timeout: float | None = 10.0) -> bool:
    """Close the shared runner and wait for all loop resources to be reclaimed."""

    with _DISPATCH_RUNNER_LOCK:
        runner = _DISPATCH_RUNNER
    if runner is None:
        return True
    return runner.shutdown(timeout=timeout)


__all__ = [
    "AsyncioRunner",
    "get_dispatch_runner",
    "request_dispatch_runner_shutdown",
    "run_awaitable",
    "shutdown_dispatch_runner",
    "submit_awaitable",
]

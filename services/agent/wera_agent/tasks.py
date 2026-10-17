"""Work that must start only once the request's data is committed.

Drafting a pack runs in the background, and it needs its own database transactions. Two things
make that awkward, and both are handled here rather than in each route:

* FastAPI's `BackgroundTasks` runs *before* a dependency with `yield` is torn down, so a task
  scheduled that way starts while the request still holds SQLite's write lock.
* Route handlers are sync, so they run in a worker thread with no event loop of their own.

So the loop is captured at startup, and the route hands its follow-up work to the `Store`
(`Store.defer`). The `store` dependency spawns it after the transaction has closed, from the
worker thread, onto the captured loop. `drain` is synchronous on purpose: tests and shutdown
both call it from outside the loop.
"""

import asyncio
from collections.abc import Coroutine
from concurrent.futures import Future, wait
from typing import Any

import logfire

_loop: asyncio.AbstractEventLoop | None = None
_pending: set[Future[Any]] = set()


def bind(loop: asyncio.AbstractEventLoop) -> None:
    """Called once from the app's lifespan."""
    global _loop
    _loop = loop


def spawn(coro: Coroutine[Any, Any, Any], *, name: str) -> None:
    if _loop is None or _loop.is_closed():
        # No app running (a unit test calling a route directly): run the work inline instead of
        # dropping it, so behaviour doesn't depend on whether a loop happens to exist.
        asyncio.run(coro)
        return
    future = asyncio.run_coroutine_threadsafe(coro, _loop)
    _pending.add(future)
    future.add_done_callback(lambda f: _finished(name, f))


def _finished(name: str, future: Future[Any]) -> None:
    _pending.discard(future)
    if not future.cancelled() and future.exception() is not None:
        # `graphs.application.advance` records its own failures; this is the net for the rest.
        logfire.error("Background task {name} failed: {error}", name=name, error=repr(future.exception()))


def drain(timeout: float = 60.0) -> None:
    """Wait for spawned work to finish, from a thread that is *not* the event loop.

    Tests call this after a request. Calling it on the loop would block the very loop the work
    needs in order to run — use `adrain` there.
    """
    while _pending:
        done, _ = wait(set(_pending), timeout=timeout)
        if not done:
            return


async def adrain(timeout: float = 30.0) -> None:
    """The same wait, awaited, for shutdown inside the app's lifespan."""
    while _pending:
        done, _ = await asyncio.wait([asyncio.wrap_future(f) for f in _pending], timeout=timeout)
        if not done:
            return


def pending() -> int:
    return len(_pending)

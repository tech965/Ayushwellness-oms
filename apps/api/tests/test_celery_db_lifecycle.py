"""Regression tests for the Celery-task async DB/event-loop lifecycle
(`app.db.session.run_with_cleanup`).

Real production incident: every Celery task ran its own work under one
`asyncio.run(...)`, then disposed the shared `AsyncEngine`/adapter HTTP
clients under a SECOND, freshly created `asyncio.run(...)` in a `finally`
block. By the time that second call ran, the first loop -- and every
asyncpg/httpx connection opened on it -- was already closed, producing:

    RuntimeError: Task <Task pending ... _cleanup_after_task() ...>
    got Future <Future pending ...> attached to a different loop
    RuntimeError: Event loop is closed

on `shiprocket.refresh_tracking` and `webhooks.recover_stuck`. These
tests exercise the REAL production `app.db.session.engine`/
`AsyncSessionLocal` (not the SQLite `db_session` fixture other tests
use) via `run_with_cleanup`, the same way every real Celery task now
does, to prove the fix against a real asyncpg connection rather than
just asserting on mocked calls.
"""

from __future__ import annotations

import asyncio

import pytest
from app.db.session import AsyncSessionLocal, run_with_cleanup
from sqlalchemy import text


async def _select_one() -> int:
    async with AsyncSessionLocal() as session:
        result = await session.execute(text("SELECT 1"))
        return result.scalar_one()


def test_async_db_operation_runs_inside_a_simulated_celery_task() -> None:
    """A single `asyncio.run(run_with_cleanup(coro))` call -- exactly what
    every real Celery task entry point does -- must be able to open a
    real async DB session, run a query, and dispose cleanly.
    """
    result = asyncio.run(run_with_cleanup(_select_one()))
    assert result == 1


def test_consecutive_simulated_tasks_do_not_cross_event_loops() -> None:
    """The actual failure mode: `engine`/adapter HTTP clients are
    process-lifetime singletons shared across every Celery task, but each
    task gets its OWN fresh event loop via its own `asyncio.run(...)`
    call. Two back-to-back "tasks" (two separate `asyncio.run()` calls,
    each with `run_with_cleanup` disposing the engine before that loop
    closes) must both succeed -- a connection pooled or left half-open by
    the first must never be reused, or fail, on the second's different
    loop.
    """
    first = asyncio.run(run_with_cleanup(_select_one()))
    second = asyncio.run(run_with_cleanup(_select_one()))
    assert first == 1
    assert second == 1


def test_cleanup_runs_even_when_the_wrapped_task_raises() -> None:
    """`run_with_cleanup` must still dispose the engine/adapters on a
    failed task (a `finally`, not an `else`) -- otherwise a task that
    raises would leak loop-bound connections for the *next* task to trip
    over, defeating the whole point of the fix.
    """

    async def _boom() -> None:
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
        raise ValueError("simulated task failure")

    with pytest.raises(ValueError, match="simulated task failure"):
        asyncio.run(run_with_cleanup(_boom()))

    # The engine must still be usable afterward -- proving disposal
    # actually completed on the same (about-to-close) loop rather than
    # being skipped or left half-done.
    result = asyncio.run(run_with_cleanup(_select_one()))
    assert result == 1

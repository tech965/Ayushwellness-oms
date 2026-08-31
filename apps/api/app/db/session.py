"""Async SQLAlchemy engine and session factory."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Awaitable

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.integrations.registry import aclose_all_adapters

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DATABASE_ECHO,
    pool_size=settings.DATABASE_POOL_SIZE,
    max_overflow=settings.DATABASE_MAX_OVERFLOW,
    pool_pre_ping=True,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that yields a request-scoped async session."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


async def run_with_cleanup[T](coro: Awaitable[T]) -> T:
    """Runs `coro` to completion, then disposes the shared `engine`'s
    connection pool and every registered integration adapter's HTTP
    client — all inside THIS SAME event loop — before returning (or
    re-raising) whatever `coro` produced.

    `engine` (this module) and every `IntegrationAdapter`
    (`app.integrations.registry`) are process-lifetime singletons shared
    across every Celery task. A pooled asyncpg connection or httpx
    connection opened while some event loop is running stores a direct
    reference to that specific loop internally, for scheduling its own
    eventual close. Every Celery task entry point must therefore run its
    work AND dispose these singletons inside the SAME `asyncio.run(...)`
    call — never two separate ones.

    A previous version of this cleanup ran the task's own work under one
    `asyncio.run(...)`, then disposed `engine` under a SECOND, freshly
    created `asyncio.run(...)` called afterward in a `finally` — but by
    then the first loop (and every connection opened on it) was already
    closed. Disposing a connection still bound to that dead loop is
    exactly what produced real production log lines on
    `shiprocket.refresh_tracking` and `webhooks.recover_stuck`:

        RuntimeError: Task <Task pending ... _cleanup_after_task() ...>
        got Future <Future pending ...> attached to a different loop
        RuntimeError: Event loop is closed

    Every task now wraps its own work coroutine in this helper and calls
    `asyncio.run()` exactly once, so disposal always happens on the
    identical loop the connections were actually opened on.
    """
    try:
        return await coro
    finally:
        await engine.dispose()
        await aclose_all_adapters()

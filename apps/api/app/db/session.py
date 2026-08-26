"""Async SQLAlchemy engine and session factory."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator

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


async def _cleanup_after_task() -> None:
    await engine.dispose()
    await aclose_all_adapters()


def dispose_engine_sync() -> None:
    """Discard everything left over from a closed event loop: pooled DB
    connections and any registered integration adapter's HTTP client.

    Every Celery task calls `asyncio.run(...)`, which creates a fresh
    event loop and closes it when the task finishes. `engine` (this
    module) and every `IntegrationAdapter` (`app.integrations.registry`)
    are single per-process objects shared across tasks, so a connection
    either one opened under one task's loop can't be reused once that
    loop is gone — the next task hitting either one gets a "different
    loop"/"event loop is closed" error (asyncpg/SQLAlchemy for the DB,
    httpx for Shopify/Shiprocket). Call this right after each
    `asyncio.run(...)` (in a `finally`) so the next task always starts
    with clean, unused connections instead of stale, loop-bound ones.

    Uses its own short-lived `asyncio.run(...)` rather than
    `engine.sync_engine.dispose()` — asyncpg's connection.close() still
    needs a greenlet/event-loop context to do its async close handshake,
    which a plain sync call outside any loop can't provide (raises
    `MissingGreenlet`). A fresh loop just for disposal gives it one.
    """
    asyncio.run(_cleanup_after_task())

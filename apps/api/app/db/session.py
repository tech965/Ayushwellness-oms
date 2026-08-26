"""Async SQLAlchemy engine and session factory."""

from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings

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


def dispose_engine_sync() -> None:
    """Discard pooled connections left over from a closed event loop.

    Every Celery task calls `asyncio.run(...)`, which creates a fresh
    event loop and closes it when the task finishes. `engine` is a
    single per-process object shared across tasks, so a connection its
    pool checked out under one task's loop can't be reused once that
    loop is gone — the next task hitting the pool gets
    "Future attached to a different loop" (asyncpg/SQLAlchemy). Call this
    synchronously right after each `asyncio.run(...)` (in a `finally`) so
    the next task's loop always starts with a clean pool instead of a
    stale, loop-bound connection.
    """
    engine.sync_engine.dispose()

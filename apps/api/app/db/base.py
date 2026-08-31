"""Declarative base and shared model mixins.

All ORM models (created starting Phase 1) inherit from `Base` and, where
appropriate, `UUIDPrimaryKeyMixin` / `TimestampMixin` so every table gets
a consistent UUID primary key and created_at/updated_at columns.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import CHAR, JSON, DateTime, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator


class Base(DeclarativeBase):
    pass


# JSON on Postgres, plain JSON (TEXT-backed) elsewhere — used for
# raw-payload/metadata columns that need to work under the SQLite test
# suite too.
JSONType = JSON().with_variant(JSONB, "postgresql")


class GUID(TypeDecorator):
    """Cross-dialect UUID: native `postgresql.UUID` on Postgres, a 32-char
    hex `CHAR` column everywhere else (SQLite, used by the test suite).
    """

    impl = CHAR
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(PostgresUUID(as_uuid=True))
        return dialect.type_descriptor(CHAR(32))

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if dialect.name == "postgresql":
            return str(value)
        if not isinstance(value, uuid.UUID):
            value = uuid.UUID(value)
        return value.hex

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if not isinstance(value, uuid.UUID):
            value = uuid.UUID(value)
        return value


class AwareDateTime(TypeDecorator):
    """`DateTime(timezone=True)` that always returns a tz-aware (UTC)
    datetime on load. SQLite silently drops tzinfo on round-trip (it has
    no native timezone-aware storage, and stores whatever naive wall-clock
    value it's handed), which breaks any comparison against
    `datetime.now(UTC)` — e.g. `RefreshToken.is_active`. Postgres already
    stores/returns aware datetimes correctly (`timestamptz` normalizes to
    UTC internally regardless of the offset written), so both methods
    below are a no-op there.

    `process_bind_param` converting to UTC *before* SQLite stores it is
    what makes `process_result_value`'s `replace(tzinfo=UTC)` on load
    correct: without it, a non-UTC-offset value (e.g. a Shopify webhook's
    IST timestamp) round-trips through SQLite with its wall-clock digits
    unchanged but its offset silently discarded — reading it back and
    reattaching UTC then produces a *different instant* than what was
    written, corrupting any later chronological comparison (confirmed
    live: this is what broke the out-of-order webhook guard in
    `OrderService.upsert_synced_order` under the SQLite test suite before
    this fix).
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is not None and value.tzinfo is not None:
            return value.astimezone(UTC)
        return value

    def process_result_value(self, value, dialect):
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value


class UUIDPrimaryKeyMixin:
    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)


class TimestampMixin:
    # eager_defaults forces server-generated defaults (created_at, and
    # updated_at's onupdate) to be fetched synchronously during the
    # already-awaited flush(), instead of being marked expired and
    # lazy-refetched on next attribute access — which would attempt
    # implicit IO outside any greenlet context under AsyncSession (e.g.
    # from Pydantic's `model_validate(obj, from_attributes=True)`).
    __mapper_args__ = {"eager_defaults": True}

    created_at: Mapped[datetime] = mapped_column(
        AwareDateTime(), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        AwareDateTime(),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

"""Reusable mixins for models that mirror an external system's record.

Applied to every model `docs/database/schema.md` documents as externally
sourced (Customer, CustomerAddress, Product, ProductVariant, Order,
Payment, Shipment, NDR, RTO, Return, Refund). Phase 1 only defines the
columns; Phase 2's sync adapters are what actually populate them via
`BaseRepository.upsert_by_external_id`.

Each concrete model is expected to add its own
`UniqueConstraint("source_system", "external_id", name=...)` in
`__table_args__` — deliberately not auto-generated here, since several
models also need other table-level constraints and a declared_attr
`__table_args__` on the mixin would just be shadowed by the subclass's
own `__table_args__` rather than merged with it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import AwareDateTime, JSONType


class SyncMetadataMixin:
    """Source-system provenance for a locally-synchronized external record."""

    source_system: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    external_created_at: Mapped[datetime | None] = mapped_column(AwareDateTime(), nullable=True)
    external_updated_at: Mapped[datetime | None] = mapped_column(AwareDateTime(), nullable=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(AwareDateTime(), nullable=True)
    sync_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    raw_external_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)


class SourceSystem:
    """Known `source_system` values. Deliberately plain strings, not a DB
    enum — new commerce/courier platforms are added without a migration.
    """

    SHOPIFY = "shopify"
    SHIPROCKET = "shiprocket"
    BLUE_DART = "blue_dart"
    MANUAL = "manual"

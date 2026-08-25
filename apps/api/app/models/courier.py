"""Courier — a database record, manually managed in Phase 1; synced from
Shiprocket's AWB-assignment response starting Phase 2.3 (`SyncMetadataMixin`
+ `upsert_by_external_id(source_system="shiprocket", external_id=courier_company_id)`).
`code` stays required/unique — for a synced courier it's a slug derived
from the Shiprocket courier name, not a Shiprocket field.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Boolean, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, JSONType, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.mixins import SyncMetadataMixin

if TYPE_CHECKING:
    from app.models.shipment import Shipment


class Courier(Base, UUIDPrimaryKeyMixin, TimestampMixin, SyncMetadataMixin):
    __tablename__ = "couriers"
    __table_args__ = (
        UniqueConstraint("source_system", "external_id", name="uq_couriers_source_external_id"),
    )

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    courier_metadata: Mapped[dict | None] = mapped_column(JSONType, nullable=True)

    shipments: Mapped[list[Shipment]] = relationship(back_populates="courier")

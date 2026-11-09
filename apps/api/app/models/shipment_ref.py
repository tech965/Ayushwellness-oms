"""OMS-owned reference from an order to its Shiprocket shipment(s).

API-first architecture: Shiprocket is the source of truth for everything
about a shipment (status, courier, tracking history, NDR/RTO). This table
keeps ONLY identifiers needed to ask Shiprocket about the right shipment
and OMS bookkeeping about actions the OMS itself performed (when it
cancelled, the state of its own push to Shopify). It stores no status,
tracking event, courier snapshot or provider payload.

For historical shipments `id` is the legacy `shipments.id` (backfilled),
so existing links, inventory movements and URLs keep working.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import GUID, AwareDateTime, Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import ShopifySyncStatus, sa_enum


class ShipmentRef(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "shipment_refs"

    order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("order_workflow.id", ondelete="CASCADE"), nullable=False, index=True
    )
    shiprocket_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    shiprocket_shipment_id: Mapped[str | None] = mapped_column(
        String(64), unique=True, nullable=True
    )
    # Identifier assigned by Shiprocket on AWB assignment (needed to query
    # tracking); a reference, not a status.
    awb: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    # Recorded when the OMS itself cancels the shipment via Shiprocket.
    cancelled_at: Mapped[datetime | None] = mapped_column(AwareDateTime(), nullable=True)

    # Bookkeeping for the OMS -> Shopify fulfilment push.
    shopify_fulfillment_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    shopify_sync_status: Mapped[ShopifySyncStatus] = mapped_column(
        sa_enum(ShopifySyncStatus, "shopify_sync_status"),
        nullable=False,
        default=ShopifySyncStatus.NOT_APPLICABLE,
    )
    shopify_sync_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    shopify_synced_at: Mapped[datetime | None] = mapped_column(AwareDateTime(), nullable=True)

    created_by: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


# --- Legacy-mode dual write ----------------------------------------------------
#
# While SHOPIFY_LIVE_MODE is off, `shipments` rows are still created and
# updated. Mirror ONLY the identifier/bookkeeping columns above so the
# reference table (and the inventory_movements FK that now points at it) is
# complete the day live mode is enabled. No status/tracking/payload field is
# copied.

from sqlalchemy import event, insert, select, update  # noqa: E402

from app.models.shipment import Shipment  # noqa: E402


def _ref_values(shipment: Shipment) -> dict:
    payload = shipment.raw_external_payload or {}
    sr_order = payload.get("order_id")
    return {
        "order_id": shipment.order_id,
        "shiprocket_order_id": str(sr_order) if sr_order else None,
        "shiprocket_shipment_id": shipment.shiprocket_shipment_id,
        "awb": shipment.awb,
        "shopify_fulfillment_id": shipment.shopify_fulfillment_id,
        "shopify_sync_status": shipment.shopify_sync_status or ShopifySyncStatus.NOT_APPLICABLE,
        "shopify_sync_error": shipment.shopify_sync_error,
        "shopify_synced_at": shipment.shopify_synced_at,
    }


@event.listens_for(Shipment, "after_insert")
def _ref_after_shipment_insert(mapper, connection, shipment: Shipment) -> None:  # noqa: ANN001
    table = ShipmentRef.__table__
    if connection.execute(select(table.c.id).where(table.c.id == shipment.id)).first():
        return
    connection.execute(
        insert(table).values(id=shipment.id, **_ref_values(shipment))  # type: ignore[arg-type]
    )


@event.listens_for(Shipment, "after_update")
def _ref_after_shipment_update(mapper, connection, shipment: Shipment) -> None:  # noqa: ANN001
    table = ShipmentRef.__table__
    connection.execute(
        update(table).where(table.c.id == shipment.id).values(**_ref_values(shipment))  # type: ignore[arg-type]
    )

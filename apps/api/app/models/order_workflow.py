"""OMS-owned workflow state for one external (Shopify) order.

API-first architecture: Shopify stays the source of truth for an order's
content (items, amounts, addresses, customer, payment, fulfilment). This
table holds ONLY state the OMS itself owns, keyed by the order's external
id -- never a copy of any Shopify field. The same row id is what
`OrderAssignment.order_id` points at (a plain reference, no FK to
`orders`), so assignments survive without a local `orders` row.

`id` is the reference UUID the UI already uses as an order id:
  * backfilled rows reuse the legacy `orders.id` (existing assignments
    and call logs keep matching), and
  * new rows use `order_ref.encode_shopify_order_ref(external_order_id)`.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import AwareDateTime, Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import OrderStatus, ShopifySyncStatus, sa_enum


class OrderWorkflow(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "order_workflow"

    source_system: Mapped[str] = mapped_column(
        String(50), nullable=False, default="shopify", server_default="shopify"
    )
    external_order_id: Mapped[str] = mapped_column(
        String(255), unique=True, nullable=False, index=True
    )

    # OMS-owned pack/ship workflow state (see ORDER_STATUS_TRANSITIONS).
    status: Mapped[OrderStatus] = mapped_column(
        sa_enum(OrderStatus, "order_status"),
        nullable=False,
        default=OrderStatus.PENDING,
        index=True,
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    confirmed_by_telecaller_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(AwareDateTime(), nullable=True)

    # State of the OMS -> Shopify confirmation-tag push (OMS bookkeeping).
    shopify_confirmation_sync_status: Mapped[ShopifySyncStatus] = mapped_column(
        sa_enum(ShopifySyncStatus, "shopify_sync_status"),
        nullable=False,
        default=ShopifySyncStatus.NOT_APPLICABLE,
    )
    shopify_confirmation_sync_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    shopify_confirmation_synced_at: Mapped[datetime | None] = mapped_column(
        AwareDateTime(), nullable=True
    )


# --- Legacy-mode dual write ----------------------------------------------------
#
# While SHOPIFY_LIVE_MODE is off, local `orders` rows are still created and
# their OMS-owned state still changes. These listeners keep the matching
# `order_workflow` row (same uuid) in step, copying ONLY OMS-owned columns,
# so (a) the FKs that now point at `order_workflow` hold for every order and
# (b) turning live mode on later starts from current, not stale, state.
# Nothing here copies any Shopify/provider field.

from sqlalchemy import event, insert, select, update  # noqa: E402

from app.models.order import Order  # noqa: E402

_MIRRORED = (
    "status",
    "notes",
    "confirmed_by_telecaller_id",
    "confirmed_at",
    "shopify_confirmation_sync_status",
    "shopify_confirmation_sync_error",
    "shopify_confirmation_synced_at",
)


def _workflow_values(order: Order) -> dict:
    return {name: getattr(order, name) for name in _MIRRORED}


@event.listens_for(Order, "after_insert")
def _workflow_after_order_insert(mapper, connection, order: Order) -> None:  # noqa: ANN001
    table = OrderWorkflow.__table__
    exists = connection.execute(select(table.c.id).where(table.c.id == order.id)).first()
    if exists is not None:
        return
    values = _workflow_values(order)
    values.update(
        id=order.id,
        source_system=order.source_system or "manual",
        external_order_id=order.shopify_order_id or order.external_id or f"local:{order.id}",
    )
    values["status"] = values["status"] or OrderStatus.PENDING
    values["shopify_confirmation_sync_status"] = (
        values["shopify_confirmation_sync_status"] or ShopifySyncStatus.NOT_APPLICABLE
    )
    connection.execute(insert(table).values(**values))  # type: ignore[arg-type]


@event.listens_for(Order, "after_update")
def _workflow_after_order_update(mapper, connection, order: Order) -> None:  # noqa: ANN001
    table = OrderWorkflow.__table__
    connection.execute(
        update(table).where(table.c.id == order.id).values(**_workflow_values(order))  # type: ignore[arg-type]
    )

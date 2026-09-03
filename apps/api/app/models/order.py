"""Order, OrderItem, OrderEvent.

`Order` holds current state for fast queries. `OrderEvent` is the
append-only timeline — never updated or deleted; every important status
change writes one (see `OrderService.transition_status`). `OrderItem`
snapshots product name/price at order time so historical orders don't
change when a product is later renamed or repriced.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Integer, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import AwareDateTime, Base, JSONType, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import (
    CancellationStatus,
    FulfillmentStatus,
    OrderStatus,
    PaymentStatus,
    PaymentType,
    sa_enum,
)
from app.models.mixins import SyncMetadataMixin

if TYPE_CHECKING:
    from app.models.customer import Customer
    from app.models.payment import Payment
    from app.models.product import ProductVariant
    from app.models.shipment import Shipment


class Order(Base, UUIDPrimaryKeyMixin, TimestampMixin, SyncMetadataMixin):
    __tablename__ = "orders"
    __table_args__ = (
        UniqueConstraint("source_system", "external_id", name="uq_orders_source_external_id"),
    )

    order_number: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    shopify_order_id: Mapped[str | None] = mapped_column(
        String(64), unique=True, nullable=True, index=True
    )
    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("customers.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # Confirmed live (EXPLAIN ANALYZE against production, 48k orders): every
    # analytics/dashboard query filters on this column (`resolve_range` in
    # analytics_service.py), and without an index this was a sequential
    # scan costing ~700ms per query alone -- the dashboard fires roughly
    # eight such queries per navigation, directly explaining the reported
    # multi-second-to-tens-of-seconds dashboard load delay.
    order_datetime: Mapped[datetime] = mapped_column(
        AwareDateTime(), nullable=False, index=True
    )
    currency: Mapped[str] = mapped_column(
        String(8), nullable=False, default="INR", server_default="INR"
    )

    subtotal: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=Decimal("0"))
    discount_amount: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=Decimal("0")
    )
    tax_amount: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=Decimal("0")
    )
    shipping_charge: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=Decimal("0")
    )
    total_amount: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=Decimal("0")
    )

    payment_type: Mapped[PaymentType] = mapped_column(
        sa_enum(PaymentType, "payment_type"), nullable=False, default=PaymentType.PREPAID
    )
    payment_status: Mapped[PaymentStatus] = mapped_column(
        sa_enum(PaymentStatus, "payment_status"),
        nullable=False,
        default=PaymentStatus.PENDING,
        index=True,
    )
    status: Mapped[OrderStatus] = mapped_column(
        sa_enum(OrderStatus, "order_status"),
        nullable=False,
        default=OrderStatus.PENDING,
        index=True,
    )
    fulfillment_status: Mapped[FulfillmentStatus] = mapped_column(
        sa_enum(FulfillmentStatus, "fulfillment_status"),
        nullable=False,
        default=FulfillmentStatus.UNFULFILLED,
    )
    cancellation_status: Mapped[CancellationStatus] = mapped_column(
        sa_enum(CancellationStatus, "cancellation_status"),
        nullable=False,
        default=CancellationStatus.NONE,
    )

    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Point-in-time snapshot of the address used for this order — not a
    # CustomerAddress FK, matching OrderItem's existing snapshot
    # convention (an order's shipping/billing address must never change
    # retroactively if the customer later edits their saved address).
    shipping_address: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    billing_address: Mapped[dict | None] = mapped_column(JSONType, nullable=True)

    customer: Mapped[Customer | None] = relationship()
    items: Mapped[list[OrderItem]] = relationship(
        back_populates="order", cascade="all, delete-orphan", order_by="OrderItem.created_at"
    )
    events: Mapped[list[OrderEvent]] = relationship(
        back_populates="order", cascade="all, delete-orphan", order_by="OrderEvent.created_at"
    )
    payments: Mapped[list[Payment]] = relationship(back_populates="order")
    shipments: Mapped[list[Shipment]] = relationship(back_populates="order")


class OrderItem(Base, UUIDPrimaryKeyMixin, TimestampMixin, SyncMetadataMixin):
    __tablename__ = "order_items"
    __table_args__ = (
        UniqueConstraint("source_system", "external_id", name="uq_order_items_source_external_id"),
    )

    order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    product_variant_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("product_variants.id", ondelete="SET NULL"), nullable=True, index=True
    )

    sku: Mapped[str] = mapped_column(String(120), nullable=False)
    product_name: Mapped[str] = mapped_column(String(500), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    discount_amount: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=Decimal("0")
    )
    tax_amount: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=Decimal("0")
    )
    total_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)

    order: Mapped[Order] = relationship(back_populates="items")
    product_variant: Mapped[ProductVariant | None] = relationship()


class OrderEvent(Base, UUIDPrimaryKeyMixin):
    """Append-only order timeline. No `updated_at` — events are never
    edited, and no repository method exposes update/delete for this model.
    """

    __tablename__ = "order_events"

    order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(
        String(50), nullable=False, default="system", server_default="system"
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    event_metadata: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    raw_external_payload: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        AwareDateTime(), server_default=func.now(), nullable=False, index=True
    )

    order: Mapped[Order] = relationship(back_populates="events")

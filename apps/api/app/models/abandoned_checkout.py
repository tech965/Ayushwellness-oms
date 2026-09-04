"""AbandonedCheckout — a Shopify checkout the customer started (entered
contact info, and in most cases picked a payment method) but never
completed. Synced read-only from Shopify (`app.integrations.shopify`,
entity_type `"abandoned_checkouts"`) via the same generic
fetch/normalize/upsert pipeline as `Order`/`Customer`/`Product` — this OMS
never invents one.

Only a checkout with a real phone or email is ever surfaced as an
assignable telecalling lead (enforced in
`app.repositories.abandoned_checkout.AbandonedCheckoutRepository.
search_query`, not here) — Shopify's Admin API has no customer-identifiable
data for a cart that never reached checkout at all, so this model
deliberately has no "anonymous cart" counterpart (see
`app.models.enums.LeadCategory`'s docstring).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import AwareDateTime, Base, JSONType, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.mixins import SyncMetadataMixin

if TYPE_CHECKING:
    from app.models.customer import Customer


class AbandonedCheckout(Base, UUIDPrimaryKeyMixin, TimestampMixin, SyncMetadataMixin):
    __tablename__ = "abandoned_checkouts"
    __table_args__ = (
        UniqueConstraint(
            "source_system", "external_id", name="uq_abandoned_checkouts_source_external_id"
        ),
    )

    shopify_checkout_id: Mapped[str | None] = mapped_column(
        String(64), unique=True, nullable=True, index=True
    )
    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("customers.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # Snapshot fields, not just the `customer` relationship — a real,
    # common case is a checkout with contact info Shopify never resolved
    # to an existing `Customer` record (guest checkout, or a phone/email
    # that doesn't match anything already synced). These are what makes a
    # checkout callable at all; a row with both null is never surfaced as
    # an assignable lead (see the module docstring).
    customer_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    customer_phone: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    customer_email: Mapped[str | None] = mapped_column(String(255), nullable=True)

    checkout_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    currency: Mapped[str] = mapped_column(
        String(8), nullable=False, default="INR", server_default="INR"
    )
    subtotal_amount: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=Decimal("0")
    )
    total_amount: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=Decimal("0")
    )
    # [{title, sku, quantity, price}, ...] — a snapshot, like `OrderItem`
    # is for a real order, but kept as JSON rather than a child table:
    # this data is display-only for the calling workflow (spec: "Products,
    # Quantity"), never joined against inventory/pricing the way a real
    # order's line items are.
    line_items: Mapped[list[dict] | None] = mapped_column(JSONType, nullable=True)

    # Set from Shopify's own `completedAt` — mirrors `Order.is_cancelled`'s
    # `bool(raw.get("cancelledAt"))` convention. A completed checkout means
    # the customer went on to actually purchase (it becomes/became a real
    # `Order` in Shopify), so it's no longer an open recovery
    # opportunity — filtered out of the assignable pool but kept for
    # reporting, never deleted (spec: never fabricate/erase real data).
    is_recovered: Mapped[bool] = mapped_column(nullable=False, default=False)

    checkout_created_at: Mapped[datetime | None] = mapped_column(AwareDateTime(), nullable=True)
    checkout_updated_at: Mapped[datetime | None] = mapped_column(AwareDateTime(), nullable=True)

    customer: Mapped[Customer | None] = relationship()

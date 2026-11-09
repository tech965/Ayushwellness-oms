"""OMS-owned workflow row for one abandoned checkout, keyed by provider +
external checkout id.

API-first architecture: Shopify is the source of truth for a checkout's
content (customer contact, items, totals, recovery link, completion). This
table stores only the reference that internal telecalling records
(`checkout_assignments`, `checkout_call_attempts`) hang off, plus OMS-owned
notes and conversion bookkeeping. It holds no customer or checkout payload.

`id` is the reference UUID the UI uses as the checkout id: the legacy
`abandoned_checkouts.id` for backfilled rows, or a reversible encoding of
the Shopify checkout id for live-only ones (see `order_ref`).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import GUID, AwareDateTime, Base, TimestampMixin, UUIDPrimaryKeyMixin


class CheckoutWorkflow(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "checkout_workflow"

    source_system: Mapped[str] = mapped_column(
        String(50), nullable=False, default="shopify", server_default="shopify"
    )
    external_checkout_id: Mapped[str] = mapped_column(
        String(255), unique=True, nullable=False, index=True
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    # OMS-recorded conversion: set when the OMS observes (live) that the
    # checkout was completed as an order. `converted_order_ref` is the
    # order's workflow reference id, never an order snapshot.
    converted_at: Mapped[datetime | None] = mapped_column(AwareDateTime(), nullable=True)
    converted_order_ref: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True)


# --- Legacy-mode dual write ----------------------------------------------------
# While live mode is off, `abandoned_checkouts` rows are still created. Mirror
# only the reference (same id, external key) so the foreign keys that now point
# at `checkout_workflow` hold. No customer/checkout data is copied.

from sqlalchemy import event, insert, select  # noqa: E402

from app.models.abandoned_checkout import AbandonedCheckout  # noqa: E402


@event.listens_for(AbandonedCheckout, "after_insert")
def _workflow_after_checkout_insert(mapper, connection, checkout: AbandonedCheckout) -> None:  # noqa: ANN001
    table = CheckoutWorkflow.__table__
    if connection.execute(select(table.c.id).where(table.c.id == checkout.id)).first():
        return
    connection.execute(
        insert(table).values(  # type: ignore[arg-type]
            id=checkout.id,
            source_system=checkout.source_system or "manual",
            external_checkout_id=(
                checkout.shopify_checkout_id or checkout.external_id or f"local:{checkout.id}"
            ),
        )
    )

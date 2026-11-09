"""Minimal webhook idempotency ledger (API-first mode).

One row per processed delivery: provider, the provider's delivery id and
the topic. NO payload, headers or customer data -- just enough to know a
delivery was already applied. The row is inserted in the SAME transaction
as the handler's workflow writes, so a handler failure rolls the claim
back (the provider's retry is processed normally) and a concurrent
duplicate blocks on the unique index and then loses.
"""

from __future__ import annotations

from sqlalchemy import Index, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import AwareDateTime, Base, UUIDPrimaryKeyMixin


class WebhookDelivery(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "webhook_deliveries"
    __table_args__ = (
        UniqueConstraint("provider", "delivery_id", name="uq_webhook_deliveries_provider_delivery"),
        Index("ix_webhook_deliveries_received_at", "received_at"),
    )

    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    delivery_id: Mapped[str] = mapped_column(String(255), nullable=False)
    topic: Mapped[str] = mapped_column(String(100), nullable=False)
    received_at = mapped_column(AwareDateTime(), server_default=func.now(), nullable=False)

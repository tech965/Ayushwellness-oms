"""Cashfree settlement records — money Cashfree has transferred (or is
about to transfer) to the merchant bank account, a fundamentally
different entity from a customer `Payment`/transaction (spec: never mix
settlement state with transaction revenue — see
`app.services.cashfree_sync_service`'s module docstring). Populated only
by `CashfreeSyncService.sync_settlements` from `POST /pg/settlements` —
never created any other way, never derived from `Payment` rows.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import AwareDateTime, Base, JSONType, TimestampMixin, UUIDPrimaryKeyMixin


class CashfreeSettlement(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "cashfree_settlements"
    __table_args__ = (
        UniqueConstraint("cf_settlement_id", name="uq_cashfree_settlements_cf_settlement_id"),
    )

    cf_settlement_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    # Cashfree's own settlement status vocabulary (SUCCESS/PENDING/
    # PENDING_WITH_CASHFREE/PENDING_WITH_BANK/FAILED) -- stored as-is
    # (free text), never coerced into `PaymentStatus`, since a settlement
    # is not a payment and the two enums must never be conflated.
    status: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
    status_description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    settlement_utr: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    settlement_initiated_on: Mapped[datetime | None] = mapped_column(
        AwareDateTime(), nullable=True
    )
    settlement_processed_on: Mapped[datetime | None] = mapped_column(
        AwareDateTime(), nullable=True, index=True
    )
    # Gross transaction amount this settlement lot covers, before any
    # deduction below -- distinct from `amount_settled` (spec: "do not
    # blindly equate transaction amount == settlement amount").
    payment_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    pg_service_charge: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    pg_service_tax: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    adjustment: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    settlement_charge: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    settlement_tax: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    # The actual amount credited to the merchant bank account -- what the
    # Payments UI's "Amount Settled" figure shows.
    amount_settled: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    raw_external_payload: Mapped[dict | None] = mapped_column(JSONType, nullable=True)

"""Shiprocket wallet: balance snapshots, the transaction ledger, and
pre-assignment wallet checks.

Hard separations (financial safeguards):
- `ShiprocketWalletTransaction` holds ONLY actual wallet movements reported
  by Shiprocket (or a manual claim that is explicitly NOT counted until
  confirmed). An estimated courier charge is never written here.
- `ShiprocketWalletCheck` holds the *estimates* made before an AWB
  assignment. It is never summed into expenses.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, ForeignKey, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import GUID, AwareDateTime, Base, JSONType, TimestampMixin, UUIDPrimaryKeyMixin


class ShiprocketWalletSnapshot(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "shiprocket_wallet_snapshots"

    balance: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(AwareDateTime(), nullable=False, index=True)
    # scheduled | manual | pre_assignment
    trigger: Mapped[str] = mapped_column(String(32), nullable=False, default="scheduled")
    raw_external_payload: Mapped[dict | None] = mapped_column(JSONType, nullable=True)


class ShiprocketWalletTransaction(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "shiprocket_wallet_transactions"

    # Stable de-duplication key: Shiprocket's own transaction reference when
    # present, else a hash of (date, type, amount, awb, description).
    dedup_key: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    transaction_ref: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    txn_date: Mapped[datetime] = mapped_column(AwareDateTime(), nullable=False, index=True)
    # recharge | freight | cod_charge | rto_freight | freight_reversal |
    # cod_reversal | refund | weight_discrepancy | other_debit | other_credit
    txn_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    direction: Mapped[str] = mapped_column(String(8), nullable=False, index=True)  # debit|credit
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)  # always positive
    balance_after: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    order_number: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    awb: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    courier_name: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    payment_reference: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # confirmed | pending | failed | unverified (manual claim, never counted)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="confirmed", index=True)
    # shiprocket_api | manual_claim
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="shiprocket_api")
    synced_at: Mapped[datetime | None] = mapped_column(AwareDateTime(), nullable=True)
    raw_external_payload: Mapped[dict | None] = mapped_column(JSONType, nullable=True)

    # Manual verification: sync never overwrites a row once this is set.
    manually_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    verified_by: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    verified_at: Mapped[datetime | None] = mapped_column(AwareDateTime(), nullable=True)
    verification_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class ShiprocketWalletCheck(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One pre-AWB-assignment wallet check. ESTIMATES only."""

    __tablename__ = "shiprocket_wallet_checks"

    shipment_ref: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    order_number: Mapped[str | None] = mapped_column(String(64), nullable=True)
    balance: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    estimated_charge: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    safety_buffer: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    required_minimum: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    shortfall: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    # allowed | blocked | allowed_unverified (balance/estimate unavailable)
    outcome: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    checked_at: Mapped[datetime] = mapped_column(AwareDateTime(), nullable=False, index=True)

"""Shiprocket shipment charges (API) and COD payout summaries (report).

Neither is a wallet movement: `ShiprocketWalletTransaction` remains the only
record of money actually debited / credited, and these are never added to it.
Per-AWB remittance amounts live only in Module 6 (`cod_remittance_lines`).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import ForeignKey, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import GUID, AwareDateTime, Base, JSONType, TimestampMixin, UUIDPrimaryKeyMixin

MONEY = Numeric(14, 2)
WEIGHT = Numeric(10, 3)


class ShiprocketShipmentCharge(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Latest charge breakdown Shiprocket reports for one AWB. `fetched_at`
    also records that the order was checked when it reported nothing."""

    __tablename__ = "shiprocket_shipment_charges"
    __table_args__ = (UniqueConstraint("awb", name="uq_shiprocket_shipment_charges_awb"),)

    awb: Mapped[str] = mapped_column(String(64), nullable=False)
    shiprocket_order_id: Mapped[str] = mapped_column(String(64), nullable=False)
    order_number: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    courier_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    zone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    freight_charges: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    cod_charges: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    applied_weight_amount: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    charged_weight_amount: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    charged_weight_amount_rto: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    applied_weight_amount_rto: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    applied_weight: Mapped[Decimal | None] = mapped_column(WEIGHT, nullable=True)
    charged_weight: Mapped[Decimal | None] = mapped_column(WEIGHT, nullable=True)
    has_charges: Mapped[bool] = mapped_column(nullable=False, default=False)
    fetched_at: Mapped[datetime] = mapped_column(AwareDateTime(), nullable=False, index=True)
    raw_external_payload: Mapped[dict | None] = mapped_column(JSONType, nullable=True)


class ShiprocketPayout(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One COD payout (CRF cycle) from an imported Shiprocket report. Amounts
    are exactly what the report states; none is calculated."""

    __tablename__ = "shiprocket_payouts"
    __table_args__ = (UniqueConstraint("dedupe_key", name="uq_shiprocket_payouts_dedupe_key"),)

    dedupe_key: Mapped[str] = mapped_column(String(64), nullable=False)
    crf_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    utr: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    payout_date: Mapped[datetime | None] = mapped_column(AwareDateTime(), nullable=True, index=True)
    status: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    cod_amount: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    freight_deduction: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    other_deductions: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    net_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    file_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("settlement_files.id", ondelete="SET NULL"), nullable=True, index=True
    )
    raw_row: Mapped[dict | None] = mapped_column(JSONType, nullable=True)

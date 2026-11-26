"""Shiprocket-reported COD collection/remittance, one row per AWB.

Populated only by `CodReconciliationService.sync_remittances` from the
existing Shiprocket client. Everything in the `reported_*`/collected/
remitted columns is what *Shiprocket says* -- it is NOT proof that money
reached the bank. `bank_credit_*` is a separate, human-entered
verification (e.g. matched against a bank statement); it stays NULL until
someone verifies it and sync never touches it.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import GUID, AwareDateTime, Base, JSONType, TimestampMixin, UUIDPrimaryKeyMixin


class CodRemittance(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "cod_remittances"

    awb: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    shiprocket_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    channel_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    # --- reported by Shiprocket -------------------------------------------
    cod_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    collected_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    collected_at: Mapped[datetime | None] = mapped_column(AwareDateTime(), nullable=True)
    remitted_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    remittance_status: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    remittance_reference: Mapped[str | None] = mapped_column(String(128), nullable=True)
    settlement_date: Mapped[datetime | None] = mapped_column(
        AwareDateTime(), nullable=True, index=True
    )
    synced_at: Mapped[datetime | None] = mapped_column(AwareDateTime(), nullable=True)
    raw_external_payload: Mapped[dict | None] = mapped_column(JSONType, nullable=True)

    # --- verified bank credit (manual; never written by sync) -------------
    bank_credit_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    bank_credit_reference: Mapped[str | None] = mapped_column(String(128), nullable=True)
    bank_verification_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    bank_credited_on: Mapped[datetime | None] = mapped_column(AwareDateTime(), nullable=True)
    bank_verified_at: Mapped[datetime | None] = mapped_column(AwareDateTime(), nullable=True)
    bank_verified_by: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

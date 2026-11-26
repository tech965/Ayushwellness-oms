"""Modules 6 & 7 -- COD / Prepaid remittance reconciliation records.

These tables hold only *evidence*: what a shipping partner or payment
gateway reported (remittance / settlement lines, from an API sync or an
uploaded report), what the bank statement shows (bank credits), and the
links and manual adjustments people made. Nothing here is derived from
order status -- a delivered COD order or a "paid" prepaid order is never
assumed to be settled; the reconciliation status is computed from this
evidence at read time (see `app.services.remittance_common`).

The original uploaded files are preserved byte-for-byte (`SettlementFile`).
Every line carries a deterministic `dedupe_key`, so re-importing the same
report (or an overlapping one) can never create a second financial entry.
Links and adjustments are reversed, never deleted, so the audit trail
stays intact.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import ForeignKey, Integer, LargeBinary, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import GUID, AwareDateTime, Base, JSONType, TimestampMixin, UUIDPrimaryKeyMixin

MONEY = Numeric(14, 2)


class SettlementFile(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """An uploaded report, stored unmodified. `module` is `cod`, `prepaid`
    or `bank`; the same bytes can never be uploaded twice for a module.
    """

    __tablename__ = "settlement_files"
    __table_args__ = (UniqueConstraint("module", "sha256", name="uq_settlement_files_module_sha"),)

    module: Mapped[str] = mapped_column(String(12), nullable=False, index=True)
    source_name: Mapped[str] = mapped_column(String(128), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str | None] = mapped_column(String(120), nullable=True)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    content: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    accepted_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    duplicate_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rejected_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    row_errors: Mapped[list | None] = mapped_column(JSONType, nullable=True)
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class BankCredit(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One credit line from a bank statement. Stays visible (and counted as
    unmatched) until linked to a remittance / settlement reference.
    """

    __tablename__ = "bank_credits"
    __table_args__ = (UniqueConstraint("dedupe_key", name="uq_bank_credits_dedupe_key"),)

    file_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("settlement_files.id", ondelete="SET NULL"), nullable=True, index=True
    )
    credit_date: Mapped[datetime | None] = mapped_column(AwareDateTime(), nullable=True, index=True)
    amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    reference: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    narration: Mapped[str | None] = mapped_column(String(500), nullable=True)
    dedupe_key: Mapped[str] = mapped_column(String(64), nullable=False)
    raw_row: Mapped[dict | None] = mapped_column(JSONType, nullable=True)


class CodRemittanceLine(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One COD remittance line reported by a shipping partner (uploaded
    settlement report). `matched_order_id` is set only on matching evidence
    (AWB or order reference) or a manual match by a permitted user.
    """

    __tablename__ = "cod_remittance_lines"
    __table_args__ = (UniqueConstraint("dedupe_key", name="uq_cod_remittance_lines_dedupe_key"),)

    file_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("settlement_files.id", ondelete="SET NULL"), nullable=True, index=True
    )
    partner_name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    awb: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    order_ref: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    remittance_reference: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    remittance_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    cod_amount_reported: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    remittance_date: Mapped[datetime | None] = mapped_column(
        AwareDateTime(), nullable=True, index=True
    )
    dedupe_key: Mapped[str] = mapped_column(String(64), nullable=False)
    matched_order_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("orders.id", ondelete="SET NULL"), nullable=True, index=True
    )
    match_method: Mapped[str | None] = mapped_column(String(16), nullable=True)
    matched_by: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    raw_row: Mapped[dict | None] = mapped_column(JSONType, nullable=True)


class GatewaySettlementLine(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One transaction line from a payment-gateway settlement report:
    gross, charges, GST, refund and the net amount settled for it.
    """

    __tablename__ = "gateway_settlement_lines"
    __table_args__ = (
        UniqueConstraint("dedupe_key", name="uq_gateway_settlement_lines_dedupe_key"),
    )

    file_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("settlement_files.id", ondelete="SET NULL"), nullable=True, index=True
    )
    gateway: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    gateway_transaction_id: Mapped[str | None] = mapped_column(
        String(128), nullable=True, index=True
    )
    order_ref: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    settlement_reference: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    gross_amount: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    gateway_charges: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("0"))
    gst_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("0"))
    refund_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=Decimal("0"))
    net_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    transaction_date: Mapped[datetime | None] = mapped_column(AwareDateTime(), nullable=True)
    settlement_date: Mapped[datetime | None] = mapped_column(
        AwareDateTime(), nullable=True, index=True
    )
    line_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    dedupe_key: Mapped[str] = mapped_column(String(64), nullable=False)
    matched_order_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("orders.id", ondelete="SET NULL"), nullable=True, index=True
    )
    match_method: Mapped[str | None] = mapped_column(String(16), nullable=True)
    matched_by: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    raw_row: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    # --- API-synchronised lines (Cashfree) -------------------------------
    # "upload" (file fallback) or "api". API lines are upserted in place by
    # their gateway event id (dedupe_key) so a re-sync never duplicates.
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="upload")
    cf_settlement_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    event_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    synced_at: Mapped[datetime | None] = mapped_column(AwareDateTime(), nullable=True)


class GatewaySyncState(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One row per synchronised gateway: when it last ran, when it last
    SUCCEEDED, what happened, and a short-lived lock so two workers (or a
    worker plus a Sync Now click) never run the same sync concurrently.
    Never stores credentials; `last_error` is a sanitised short message.
    """

    __tablename__ = "gateway_sync_states"
    __table_args__ = (UniqueConstraint("gateway", name="uq_gateway_sync_states_gateway"),)

    gateway: Mapped[str] = mapped_column(String(32), nullable=False)
    last_attempt_at: Mapped[datetime | None] = mapped_column(AwareDateTime(), nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(AwareDateTime(), nullable=True)
    last_status: Mapped[str | None] = mapped_column(String(24), nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    last_trigger: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # Settlement-initiated/processed time up to which data has been pulled.
    synced_through: Mapped[datetime | None] = mapped_column(AwareDateTime(), nullable=True)
    last_result: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    last_webhook_at: Mapped[datetime | None] = mapped_column(AwareDateTime(), nullable=True)
    lock_until: Mapped[datetime | None] = mapped_column(AwareDateTime(), nullable=True)


class BankCreditLink(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Evidence that (part of) a bank credit pays a remittance / settlement
    `reference` (partner remittance id or gateway settlement UTR). Created
    automatically when the references are equal, or manually. Reversed
    (`reversed_at`) rather than deleted.
    """

    __tablename__ = "bank_credit_links"
    __table_args__ = (
        UniqueConstraint(
            "bank_credit_id", "module", "reference", name="uq_bank_credit_links_credit_ref"
        ),
    )

    bank_credit_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("bank_credits.id", ondelete="CASCADE"), nullable=False, index=True
    )
    module: Mapped[str] = mapped_column(String(12), nullable=False, index=True)
    reference: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    method: Mapped[str] = mapped_column(String(10), nullable=False)
    linked_by: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    # Manual links only: `note` is the mandatory reason and `bank_reference` the
    # bank's own reference / UTR the ADMIN checked.
    note: Mapped[str | None] = mapped_column(String(255), nullable=True)
    bank_reference: Mapped[str | None] = mapped_column(String(128), nullable=True)
    reversed_at: Mapped[datetime | None] = mapped_column(AwareDateTime(), nullable=True)
    reversed_by: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class ReconciliationAdjustment(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A manual, reasoned adjustment on one order (e.g. an agreed write-off
    of a short remittance). `amount` counts toward what is considered
    settled for the difference calculation only -- it never counts as bank
    evidence. Reversed, not deleted.
    """

    __tablename__ = "reconciliation_adjustments"

    module: Mapped[str] = mapped_column(String(12), nullable=False, index=True)
    order_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    reason: Mapped[str] = mapped_column(String(255), nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    reversed_at: Mapped[datetime | None] = mapped_column(AwareDateTime(), nullable=True)
    reversed_by: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

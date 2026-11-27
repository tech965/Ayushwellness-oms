"""Schemas for Modules 6 & 7 remittance reconciliation."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field

from app.schemas.response import PaginatedResponse


class KpiAmount(BaseModel):
    count: int
    amount: Decimal


class PercentageItem(BaseModel):
    key: str
    label: str
    kind: str  # "count" | "value"
    numerator: Decimal
    denominator: Decimal
    numerator_label: str
    denominator_label: str
    value: float | None
    drill: str | None = None


class StatusCount(BaseModel):
    status: str
    label: str
    count: int
    amount: Decimal
    percent_of_orders: float | None


class DrillBreakdown(BaseModel):
    label: str
    count: int
    amount: Decimal


class DrillTotals(BaseModel):
    """Totals over every record of a drilldown (all pages). `base_*` ignores
    the search term, so it is the figure the KPI card shows."""

    measure: str
    count: int
    amount: Decimal
    base_count: int
    base_amount: Decimal
    breakdown: list[DrillBreakdown] = []


class BankPositionFields(BaseModel):
    """Bank confirmation and adjustment effect, per order."""

    difference_before_adjustments: Decimal
    adjustment_effect: str
    bank_expected: Decimal
    bank_statement_amount: Decimal
    bank_manual_amount: Decimal
    bank_remaining: Decimal
    bank_over: Decimal
    bank_status: str
    bank_status_label: str
    bank_evidence: str | None = None


class CodRecordRow(BankPositionFields):
    order_id: uuid.UUID
    order_number: str
    awb: str | None
    customer_name: str | None
    order_amount: Decimal
    payment_status: str | None = None
    delivery_status: str | None = None
    contribution: Decimal
    partner: str
    service_name: str | None
    order_datetime: datetime
    delivery_date: datetime | None
    expected_amount: Decimal
    remittance_amount: Decimal
    amount_received: Decimal
    adjustment_amount: Decimal
    difference: Decimal
    remittance_reference: str | None
    remittance_date: datetime | None
    bank_credit_date: datetime | None
    status: str
    status_label: str
    source: str | None
    age_days: int
    line_count: int
    # True: no remittance AMOUNT is known (status stays "pending") but Shiprocket's
    # order API reports a payout reference or date. Counts as Payment Initiated.
    payout_reported: bool = False


class PrepaidRecordRow(BankPositionFields):
    unverified_line_count: int = 0
    unverified_amount: Decimal = Decimal("0.00")
    unverified_statuses: str | None = None
    order_id: uuid.UUID
    order_number: str
    customer_name: str | None
    gateway: str
    gateway_transaction_id: str | None
    cashfree_payment_id: str | None = None
    settlement_id: str | None = None
    payment_status: str | None = None
    delivery_status: str | None = None
    contribution: Decimal
    transaction_date: datetime | None
    order_datetime: datetime
    order_amount: Decimal
    gross_amount: Decimal
    gateway_charges: Decimal
    gst_amount: Decimal
    refund_amount: Decimal
    expected_net: Decimal
    actual_settlement: Decimal
    bank_confirmed_settlement: Decimal
    adjustment_amount: Decimal
    bank_reference: str | None
    settlement_date: datetime | None
    bank_credit_date: datetime | None
    difference: Decimal
    settlement_gap: Decimal
    status: str
    status_label: str
    payment_recorded: bool
    line_count: int
    age_days: int


class UnmatchedRow(BaseModel):
    kind: str
    line_status: str | None = None
    verification: str | None = None
    id: uuid.UUID
    contribution: Decimal
    settlement_id: str | None = None
    cashfree_payment_id: str | None = None
    gross_amount: Decimal | None = None
    gateway_charges: Decimal | None = None
    gst_amount: Decimal | None = None
    refund_amount: Decimal | None = None
    partner: str | None = None
    gateway: str | None = None
    awb: str | None = None
    transaction_id: str | None = None
    order_ref: str | None = None
    reference: str | None
    amount: Decimal
    date: datetime | None
    reason: str
    matched_order_id: uuid.UUID | None = None


class BankCreditRow(BaseModel):
    id: uuid.UUID
    credit_date: datetime | None
    amount: Decimal
    reference: str | None
    narration: str | None
    linked_amount: Decimal
    unlinked_amount: Decimal
    linked_references: str | None
    link_state: str
    statement_matched_amount: Decimal
    manually_verified_amount: Decimal
    links: list[dict[str, Any]] = []


class SettlementFileRow(BaseModel):
    id: uuid.UUID
    module: str
    source_name: str
    filename: str
    size_bytes: int
    row_count: int
    accepted_count: int
    duplicate_count: int
    rejected_count: int
    row_errors: list[Any] | None = None
    created_at: datetime


class SummaryResponse(BaseModel):
    kpis: dict[str, dict[str, Any]]
    percentages: list[PercentageItem]
    status_breakdown: list[StatusCount]
    partners: list[dict[str, Any]] | None = None
    gateways: list[dict[str, Any]] | None = None
    denominators: dict[str, Any]
    # COD only: which remittance / bank evidence is loaded (see
    # `CodRemittanceReconciliationService._data_coverage`).
    data_coverage: dict[str, Any] | None = None


class UploadResult(BaseModel):
    file_id: uuid.UUID
    filename: str
    rows_read: int
    accepted: int
    duplicates_skipped: int | None = None
    duplicates_or_non_credits_skipped: int | None = None
    rejected: int
    auto_matched_to_orders: int | None = None
    unmatched: int | None = None
    bank_credits_linked: int | None = None
    unverified_rows: int | None = None
    unsuccessful_rows: int | None = None
    statuses_updated: int | None = None
    auto_linked_cod: int | None = None
    auto_linked_prepaid: int | None = None
    row_errors: list[Any]
    column_mapping: dict[str, str] = {}
    ignored_columns: list[str] = []


class UploadPreview(BaseModel):
    """What the server would read from a file, before anything is imported."""

    headers: list[str]
    suggested_mapping: dict[str, str]
    fields: list[dict[str, Any]]  # {field, label, template_header}
    required_groups: list[list[str]]
    missing: list[str]
    ambiguous: dict[str, list[str]]
    ignored: list[str]
    sample_rows: list[list[str]]
    can_import: bool
    problems: list[str]


class ManualMatchRequest(BaseModel):
    order_id: uuid.UUID
    # COD manual match: the UI must confirm and accept any leftover difference.
    confirm: bool = False
    acknowledge_difference: bool = False


class BankLinkRequest(BaseModel):
    """Manual bank verification: every field is mandatory."""

    bank_credit_id: uuid.UUID
    reference: str = Field(min_length=1, max_length=128)
    bank_reference: str = Field(min_length=1, max_length=128)
    amount: Decimal = Field(gt=0)
    reason: str = Field(min_length=3, max_length=255)


class AdjustmentRequest(BaseModel):
    order_id: uuid.UUID
    amount: Decimal
    reason: str = Field(min_length=3, max_length=255)


class SourceStatus(BaseModel):
    key: str
    label: str
    api_supported: bool
    api_configured: bool
    upload_supported: bool = True
    last_synced_at: datetime | None = None
    note: str
    # API sources only: not_configured | never_synced | connected | degraded | error
    connection_status: str | None = None


class CashfreeSyncRequest(BaseModel):
    """Optional explicit window for Sync Now; by default the sync resumes
    from the last successful sync (with a small look-back)."""

    date_from: datetime | None = None
    date_to: datetime | None = None


class SourcesResponse(BaseModel):
    cod: list[SourceStatus]
    prepaid: list[SourceStatus]


class DrilldownResponse[Row](PaginatedResponse[Row]):
    totals: DrillTotals

"""Module 7 -- Prepaid Remittance Reconciliation.

Reconciles prepaid order collections with payment-gateway settlements and
bank credits.

Dataset: prepaid orders placed in the selected date range (optionally one
gateway). Per order:

- gross collection = captured payments (`paid`, `partially_refunded`,
  `refunded`) recorded for the order -- a *successful payment is not a
  settlement*; if an order has no payment row at all it is counted as
  "payment not recorded" using the order total only when the order itself
  says it is paid;
- charges / GST / net settled = what uploaded gateway settlement lines
  report for that order (never estimated);
- refunds = completed refunds recorded for the order;
- expected net settlement = gross - charges - GST - refunds;
- settlement received = the successful net settlement the gateway REPORTS;
- bank confirmed = the part of it whose settlement reference / UTR is covered
  by linked bank credits -- a gateway "success" alone never counts;
- settlement difference = expected net - settlement received.

An order with a captured payment and no settlement line is `pending`.
`matched` needs the reported settlement to equal the expected net AND the
bank credit evidence; a partial amount is `short`.

Gateways: the existing Cashfree integration (settlement-lot sync) plus any
gateway whose settlement report is uploaded. No other gateway integration
is assumed.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal
from io import BytesIO
from typing import Any

from openpyxl import Workbook
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import NotFoundError, ValidationError
from app.models.auth import User
from app.models.cashfree_settlement import CashfreeSettlement
from app.models.enums import PaymentStatus, PaymentType, RefundStatus
from app.models.order import Order
from app.models.payment import Payment, PaymentTransaction
from app.models.refund import Refund
from app.models.remittance_reconciliation import (
    GatewaySettlementLine,
    ReconciliationAdjustment,
)
from app.schemas.common import PageParams
from app.services import remittance_upload_parser as parser
from app.services.remittance_common import (
    AGEING_BUCKETS,
    BANK_EXPORT_COLUMNS,
    BANK_MEASURES,
    BANK_SELECTORS,
    FAILED_LINE_STATES,
    STATUS_LABELS,
    TOLERANCE,
    VERIFICATION_LABELS,
    ZERO,
    BankEvidence,
    add_bank_summary_sheet,
    add_definitions_sheet,
    adjustment_effect,
    adjustments_for,
    ageing_bucket,
    allocate_to_lines,
    audit,
    auto_link_bank_credits,
    bank_and_adjustment_kpis,
    bank_links_for,
    bank_position,
    chunks,
    dec,
    drill_totals,
    drilldown_workbook,
    evidence_from,
    export_value,
    fmt_dt,
    is_successful,
    load_bank_evidence,
    load_ledger,
    money,
    neutralise_formulas,
    norm_ref,
    paginate,
    percentage_item,
    reference_lines,
    reference_positions,
    round_pct,
    store_upload,
    to_ist_date,
    verification_of,
)

MODULE = "prepaid"
PAID_LIKE = (PaymentStatus.PAID, PaymentStatus.PARTIALLY_REFUNDED, PaymentStatus.REFUNDED)
PREPAID_STATUSES = (
    "pending",
    "partially_reconciled",
    "matched",
    "short",
    "excess",
    "failed",
    "refunded",
)
# Which statuses count is defined once, in `remittance_common`
# (SUCCESS_LINE_STATES is an allowlist; everything unfamiliar is "unverified").
__all__ = ["FAILED_LINE_STATES", "PrepaidRemittanceReconciliationService", "prepaid_status"]

SELECTORS = {
    "orders": lambda r: True,
    "gross": lambda r: r["gross_amount"] > 0,
    "charges": lambda r: r["gateway_charges"] != 0,
    "gst": lambda r: r["gst_amount"] != 0,
    "refunds": lambda r: r["refund_amount"] != 0,
    "expected_net": lambda r: r["gross_amount"] > 0,
    "reported_settlement": lambda r: r["actual_settlement"] != 0,
    "bank_settlement": lambda r: r["bank_confirmed_settlement"] != 0,
    # Every record that adds to (or subtracts from) "expected net - received".
    "difference": lambda r: r["expected_net"] != r["actual_settlement"],
    "reconciled": lambda r: r["status"] == "matched",
    "partial": lambda r: r["status"] == "partially_reconciled",
    # Money was captured but nothing has settled. Unpaid orders are not
    # "settlement pending" (the status filter still lists them).
    "pending": lambda r: r["status"] == "pending" and r["gross_amount"] > 0,
    "short": lambda r: r["status"] == "short",
    "excess": lambda r: r["status"] == "excess",
    "failed": lambda r: r["status"] == "failed",
    "refunded": lambda r: r["status"] == "refunded",
    # Orders with a settlement line whose status is blank / unfamiliar.
    "unverified": lambda r: r["unverified_line_count"] > 0,
    **BANK_SELECTORS,
}

# What ONE record contributes to the card that opened a drilldown. Summed over
# every record of the selection it equals the card's KPI (see the tests).
GROSS_MEASURE = ("Gross collected", lambda r: r["gross_amount"])
MEASURES = {
    "orders": ("Order amount", lambda r: r["order_amount"]),
    "charges": ("Gateway charges", lambda r: r["gateway_charges"]),
    "gst": ("GST", lambda r: r["gst_amount"]),
    "refunds": ("Refunds", lambda r: r["refund_amount"]),
    "expected_net": ("Expected net", lambda r: r["expected_net"]),
    "reported_settlement": (
        "Settlement received (gateway-reported)",
        lambda r: r["actual_settlement"],
    ),
    "bank_settlement": ("Prepaid bank confirmed", lambda r: r["bank_confirmed_settlement"]),
    "difference": (
        "Expected net minus settlement received",
        lambda r: r["expected_net"] - r["actual_settlement"],
    ),
    "reconciled": ("Expected net", lambda r: r["expected_net"]),
    "failed": ("Order amount", lambda r: r["order_amount"]),
    "short": ("Short amount", lambda r: -r["difference"]),
    "excess": ("Excess amount", lambda r: r["difference"]),
    "unverified": ("Unverified amount (not counted)", lambda r: r["unverified_amount"]),
    **BANK_MEASURES,
}


@dataclass
class Filters:
    date_from: datetime | None = None
    date_to: datetime | None = None
    gateway: str | None = None
    status: str | None = None
    q: str | None = None
    age_bucket: str | None = None
    settled_on: str | None = None
    order_id: uuid.UUID | None = None


def _gateway_name(value: str | None) -> str:
    text = (value or "").strip()
    return text.title() if text else "Not recorded"


def prepaid_status(
    *,
    captured: bool,
    failed: bool,
    gross: Decimal,
    refunds: Decimal,
    has_lines: bool,
    expected_net: Decimal,
    settled: Decimal,
    bank_confirmed: Decimal,
    fully_covered: bool = True,
    adjustment: Decimal = ZERO,
) -> str:
    """`settled` is the gateway-reported settlement alone. A manual adjustment
    counts toward the status difference, but it is not bank money: Matched needs
    the bank to cover the gateway-reported settlement, nothing more.
    """
    if not captured:
        return "failed" if failed else "pending"
    if refunds >= gross - TOLERANCE and gross > 0:
        return "refunded"
    if not has_lines and settled + adjustment == 0:
        return "pending"
    diff = settled + adjustment - expected_net
    if diff < -TOLERANCE:
        return "short"
    if diff > TOLERANCE:
        return "excess"
    # The tolerance covers rounding only: matched always needs a bank credit.
    return (
        "matched"
        if fully_covered
        and bank_confirmed > 0
        and bank_confirmed >= settled - TOLERANCE
        and settled > 0
        else "partially_reconciled"
    )


class PrepaidRemittanceReconciliationService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ------------------------------------------------------------ ingestion

    async def import_report(
        self,
        *,
        gateway: str,
        filename: str,
        content_type: str | None,
        data: bytes,
        user: User | None,
        column_map: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        if not gateway.strip():
            raise ValidationError("Payment gateway name is required.")
        gateway_name = gateway.strip()[:64]
        parsed = parser.parse_gateway_settlements(filename, data, gateway_name, column_map)
        record = await store_upload(
            self.session,
            module=MODULE,
            source_name=gateway_name,
            filename=filename,
            content_type=content_type,
            data=data,
            user=user,
        )
        keys = [r["dedupe_key"] for r in parsed.rows]
        existing: dict[str, GatewaySettlementLine] = {}
        for part in chunks(keys):
            for found in (
                await self.session.execute(
                    select(GatewaySettlementLine).where(
                        GatewaySettlementLine.dedupe_key.in_(list(part))
                    )
                )
            ).scalars():
                existing[found.dedupe_key] = found
        added: list[GatewaySettlementLine] = []
        restated: list[GatewaySettlementLine] = []
        status_changes: list[dict[str, str | None]] = []
        duplicates = parsed.skipped
        for row in parsed.rows:
            old = existing.get(row["dedupe_key"])
            if old is not None:
                # A corrected report may give an UNVERIFIED line a recognised
                # status. Nothing else about an imported line ever changes.
                if (
                    old.source == "upload"
                    and verification_of(old.line_status) == "unverified"
                    and verification_of(row["line_status"]) != "unverified"
                ):
                    status_changes.append(
                        {"line": str(old.id), "from": old.line_status, "to": row["line_status"]}
                    )
                    old.line_status = row["line_status"]
                    restated.append(old)
                duplicates += 1
                continue
            line = GatewaySettlementLine(file_id=record.id, gateway=gateway_name, **row)
            self.session.add(line)
            added.append(line)
        await self.session.flush()
        matched = await self._auto_match(added)
        linked = await auto_link_bank_credits(
            self.session,
            MODULE,
            (ln.settlement_reference for ln in [*added, *restated]),
            user=user,
        )
        record.row_count = parsed.total_rows
        record.accepted_count = len(added)
        record.duplicate_count = duplicates
        record.rejected_count = len(parsed.errors)
        record.row_errors = parsed.errors[:100]
        await audit(
            self.session,
            user,
            "remittance.prepaid.upload",
            "settlement_file",
            record.id,
            new={
                "file": filename,
                "gateway": gateway_name,
                "accepted": len(added),
                "status_changes": status_changes[:200],
                "status_change_count": len(status_changes),
            },
        )
        await self.session.commit()
        return {
            "file_id": record.id,
            "filename": filename,
            "rows_read": parsed.total_rows,
            "accepted": len(added),
            "duplicates_skipped": duplicates,
            "rejected": len(parsed.errors),
            "auto_matched_to_orders": matched,
            "unmatched": len(added) - matched,
            "bank_credits_linked": linked,
            "unverified_rows": sum(
                1 for ln in added if verification_of(ln.line_status) == "unverified"
            ),
            "unsuccessful_rows": sum(
                1 for ln in added if verification_of(ln.line_status) == "unsuccessful"
            ),
            "statuses_updated": len(status_changes),
            "row_errors": parsed.errors[:50],
            "column_mapping": parsed.column_mapping,
            "ignored_columns": parsed.ignored_columns,
        }

    async def match_lines(self, lines: list[GatewaySettlementLine]) -> int:
        """Public entry for other services (the Cashfree API sync) to run
        the same exact-id auto-matching used for uploaded files."""
        return await self._auto_match(lines)

    async def _auto_match(self, lines: list[GatewaySettlementLine]) -> int:
        """Gateway transaction id first, then order id. Never fuzzy. Only
        prepaid orders are matched.
        """
        todo = [ln for ln in lines if ln.matched_order_id is None]
        if not todo:
            return 0
        txn_map: dict[str, uuid.UUID] = {}
        for part in chunks(
            sorted({ln.gateway_transaction_id for ln in todo if ln.gateway_transaction_id})
        ):
            for txn, order_id in (
                await self.session.execute(
                    select(Payment.external_transaction_id, Payment.order_id).where(
                        Payment.external_transaction_id.in_(part)
                    )
                )
            ).all():
                txn_map[txn.strip().lower()] = order_id
            for txn, order_id in (
                await self.session.execute(
                    select(PaymentTransaction.gateway_transaction_id, Payment.order_id)
                    .join(Payment, Payment.id == PaymentTransaction.payment_id)
                    .where(PaymentTransaction.gateway_transaction_id.in_(part))
                )
            ).all():
                if txn:
                    txn_map.setdefault(txn.strip().lower(), order_id)
        refs = {ln.order_ref.lstrip("#") for ln in todo if ln.order_ref}
        num_map: dict[str, uuid.UUID] = {}
        for part in chunks(sorted(refs | {f"#{r}" for r in refs})):
            for number, order_id in (
                await self.session.execute(
                    select(Order.order_number, Order.id).where(
                        Order.order_number.in_(part), Order.payment_type == PaymentType.PREPAID
                    )
                )
            ).all():
                num_map[number.lstrip("#").strip().lower()] = order_id
        prepaid_ids = set()
        for part in chunks(sorted({str(v) for v in txn_map.values()})):
            prepaid_ids.update(
                (
                    await self.session.execute(
                        select(Order.id).where(
                            Order.id.in_([uuid.UUID(v) for v in part]),
                            Order.payment_type == PaymentType.PREPAID,
                        )
                    )
                )
                .scalars()
                .all()
            )
        count = 0
        for ln in todo:
            order_id = None
            method = "txn_id"
            if ln.gateway_transaction_id:
                cand = txn_map.get(ln.gateway_transaction_id.strip().lower())
                if cand in prepaid_ids:
                    order_id = cand
            if order_id is None and ln.order_ref:
                order_id = num_map.get(ln.order_ref.lstrip("#").strip().lower())
                method = "order_ref"
            if order_id is not None:
                ln.matched_order_id = order_id
                ln.match_method = method
                count += 1
        await self.session.flush()
        return count

    async def rematch(self, user: User | None) -> dict[str, int]:
        lines = (
            (
                await self.session.execute(
                    select(GatewaySettlementLine).where(
                        GatewaySettlementLine.matched_order_id.is_(None)
                    )
                )
            )
            .scalars()
            .all()
        )
        matched = await self._auto_match(list(lines))
        refs = (
            await self.session.execute(
                select(GatewaySettlementLine.settlement_reference).distinct()
            )
        ).scalars()
        linked = await auto_link_bank_credits(self.session, MODULE, list(refs), user=user)
        await audit(
            self.session,
            user,
            "remittance.prepaid.rematch",
            "gateway_settlement_line",
            "bulk",
            new={"matched": matched, "bank_links": linked},
        )
        await self.session.commit()
        return {"matched": matched, "still_unmatched": len(lines) - matched, "bank_links": linked}

    async def manual_match(self, line_id: uuid.UUID, order_id: uuid.UUID, user: User) -> None:
        line = await self.session.get(GatewaySettlementLine, line_id)
        if line is None:
            raise NotFoundError("Settlement line not found.")
        order = await self.session.get(Order, order_id)
        if order is None:
            raise NotFoundError("Order not found.")
        if order.payment_type != PaymentType.PREPAID:
            raise ValidationError("Only a prepaid order can be matched to a gateway settlement.")
        previous = {
            "matched_order_id": str(line.matched_order_id) if line.matched_order_id else None
        }
        line.matched_order_id = order_id
        line.match_method = "manual"
        line.matched_by = user.id
        await audit(
            self.session,
            user,
            "remittance.prepaid.manual_match",
            "gateway_settlement_line",
            line.id,
            previous=previous,
            new={"matched_order_id": str(order_id)},
        )
        await self.session.commit()

    async def unmatch(self, line_id: uuid.UUID, user: User) -> None:
        line = await self.session.get(GatewaySettlementLine, line_id)
        if line is None:
            raise NotFoundError("Settlement line not found.")
        previous = {
            "matched_order_id": str(line.matched_order_id) if line.matched_order_id else None
        }
        line.matched_order_id = None
        line.match_method = None
        line.matched_by = None
        await audit(
            self.session,
            user,
            "remittance.prepaid.unmatch",
            "gateway_settlement_line",
            line.id,
            previous=previous,
        )
        await self.session.commit()

    async def add_adjustment(
        self, *, order_id: uuid.UUID, amount: Decimal, reason: str, user: User
    ) -> ReconciliationAdjustment:
        if not reason.strip():
            raise ValidationError("A reason is required for a manual adjustment.")
        if amount == 0:
            raise ValidationError("The adjustment amount cannot be zero.")
        if await self.session.get(Order, order_id) is None:
            raise NotFoundError("Order not found.")
        adj = ReconciliationAdjustment(
            module=MODULE,
            order_id=order_id,
            amount=money(amount),
            reason=reason.strip()[:255],
            created_by=user.id,
        )
        self.session.add(adj)
        await self.session.flush()
        await audit(
            self.session,
            user,
            "remittance.prepaid.adjustment",
            "reconciliation_adjustment",
            adj.id,
            new={"order_id": str(order_id), "amount": str(adj.amount), "reason": adj.reason},
        )
        await self.session.commit()
        return adj

    # ---------------------------------------------------------------- load

    async def _records(self, f: Filters) -> tuple[list[dict], dict[str, Any]]:
        stmt = (
            select(Order)
            .where(Order.payment_type == PaymentType.PREPAID)
            .options(selectinload(Order.customer), selectinload(Order.payments))
        )
        if f.date_from:
            stmt = stmt.where(Order.order_datetime >= f.date_from)
        if f.date_to:
            stmt = stmt.where(Order.order_datetime <= f.date_to)
        if f.order_id is not None:
            stmt = stmt.where(Order.id == f.order_id)
        orders = (await self.session.execute(stmt)).scalars().all()
        order_ids = [o.id for o in orders]

        refunds: dict[uuid.UUID, Decimal] = defaultdict(lambda: ZERO)
        lines_by_order: dict[uuid.UUID, list[GatewaySettlementLine]] = defaultdict(list)
        unverified_by_order: dict[uuid.UUID, list[GatewaySettlementLine]] = defaultdict(list)
        adjustments: dict[uuid.UUID, Decimal] = defaultdict(lambda: ZERO)
        for part in chunks(order_ids):
            ids = list(part)
            for order_id, amount in (
                await self.session.execute(
                    select(Refund.order_id, Refund.amount).where(
                        Refund.order_id.in_(ids), Refund.status == RefundStatus.COMPLETED
                    )
                )
            ).all():
                refunds[order_id] += dec(amount)
            for ln in (
                (
                    await self.session.execute(
                        select(GatewaySettlementLine).where(
                            GatewaySettlementLine.matched_order_id.in_(ids)
                        )
                    )
                )
                .scalars()
                .all()
            ):
                kind = verification_of(ln.line_status)
                if kind == "successful":
                    lines_by_order[ln.matched_order_id].append(ln)
                elif kind == "unverified":
                    unverified_by_order[ln.matched_order_id].append(ln)
            for adj in (
                (
                    await self.session.execute(
                        select(ReconciliationAdjustment).where(
                            ReconciliationAdjustment.module == MODULE,
                            ReconciliationAdjustment.order_id.in_(ids),
                            ReconciliationAdjustment.reversed_at.is_(None),
                        )
                    )
                )
                .scalars()
                .all()
            ):
                adjustments[adj.order_id] += dec(adj.amount)

        evidence = evidence_from(await load_ledger(self.session), MODULE)
        refs = {ln.settlement_reference for ls in lines_by_order.values() for ln in ls}
        line_bank = allocate_to_lines(await reference_lines(self.session, MODULE, refs), evidence)

        gateway_filter = f.gateway.strip().casefold() if f.gateway else None
        records: list[dict] = []
        for order in orders:
            payments = list(order.payments)
            paid = [p for p in payments if p.status in PAID_LIKE]
            captured = bool(paid)
            payment_recorded = bool(payments)
            gross = sum((dec(p.amount) for p in paid), ZERO)
            if not payments and order.payment_status in PAID_LIKE:
                gross, captured = dec(order.total_amount), True
            failed = (
                bool(payments)
                and not captured
                and any(p.status == PaymentStatus.FAILED for p in payments)
            )
            gateway = _gateway_name((paid or payments)[0].provider if payments else None)
            txns = [
                p.external_transaction_id for p in (paid or payments) if p.external_transaction_id
            ]
            # Cashfree's own payment id: from a Cashfree payment, or from a
            # settlement row the Cashfree API returned. Never guessed.
            cashfree_ids = [
                p.external_transaction_id
                for p in (paid or payments)
                if p.external_transaction_id and (p.provider or "").strip().lower() == "cashfree"
            ]
            paid_on = max((p.paid_at for p in paid if p.paid_at), default=None)
            lines = lines_by_order.get(order.id, [])
            unverified = unverified_by_order.get(order.id, [])
            if (lines or unverified) and gateway == "Not recorded":
                gateway = _gateway_name((lines or unverified)[0].gateway)
            if gateway_filter and gateway.casefold() != gateway_filter:
                continue
            charges = sum((dec(ln.gateway_charges) for ln in lines), ZERO)
            gst = sum((dec(ln.gst_amount) for ln in lines), ZERO)
            settled = sum((dec(ln.net_amount) for ln in lines), ZERO)
            adj = adjustments.get(order.id, ZERO)
            refund_total = refunds.get(order.id, ZERO)
            expected_net = gross - charges - gst - refund_total
            references: list[str] = []
            confirmed = ZERO
            manual = ZERO
            covered = bool(lines)
            bank_date: datetime | None = None
            settled_on: datetime | None = None
            for ln in lines:
                if ln.settlement_reference and ln.settlement_reference not in references:
                    references.append(ln.settlement_reference)
                if ln.settlement_date and (settled_on is None or ln.settlement_date > settled_on):
                    settled_on = ln.settlement_date
                applied = line_bank.get(ln.id)
                covered = covered and applied is not None and applied.covered
                if applied is None or applied.confirmed == 0:
                    continue
                confirmed += applied.confirmed
                manual += applied.manual
                cd = evidence.credit_dates.get(applied.reference)
                if cd and (bank_date is None or cd > bank_date):
                    bank_date = cd
            settlement_ids: list[str] = []
            for ln in lines_by_order.get(order.id, []):
                extra = ln.gateway_transaction_id
                if extra and extra not in txns:
                    txns.append(extra)
                if extra and ln.source == "api" and extra not in cashfree_ids:
                    cashfree_ids.append(extra)
                if ln.cf_settlement_id and ln.cf_settlement_id not in settlement_ids:
                    settlement_ids.append(ln.cf_settlement_id)
            status = prepaid_status(
                captured=captured,
                failed=failed,
                gross=gross,
                refunds=refund_total,
                has_lines=bool(lines),
                expected_net=expected_net,
                settled=settled,
                adjustment=adj,
                bank_confirmed=confirmed,
                fully_covered=covered,
            )
            before = settled - expected_net if captured else ZERO
            status_before = prepaid_status(
                captured=captured,
                failed=failed,
                gross=gross,
                refunds=refund_total,
                has_lines=bool(lines),
                expected_net=expected_net,
                settled=settled,
                bank_confirmed=confirmed,
                fully_covered=covered,
            )
            records.append(
                {
                    "order_id": order.id,
                    "order_number": order.order_number,
                    "customer_name": order.customer.full_name if order.customer else None,
                    "gateway": gateway,
                    "gateway_transaction_id": ", ".join(txns) or None,
                    "cashfree_payment_id": ", ".join(cashfree_ids) or None,
                    "settlement_id": ", ".join(settlement_ids) or None,
                    "payment_status": _enum_value(order.payment_status),
                    "delivery_status": _enum_value(order.fulfillment_status),
                    "transaction_date": paid_on or order.order_datetime,
                    "order_datetime": order.order_datetime,
                    "order_amount": money(dec(order.total_amount)),
                    "gross_amount": money(gross),
                    "gateway_charges": money(charges),
                    "gst_amount": money(gst),
                    "refund_amount": money(refund_total),
                    "expected_net": money(expected_net) if gross > 0 else money(ZERO),
                    "actual_settlement": money(settled),
                    "bank_confirmed_settlement": money(confirmed),
                    "adjustment_amount": money(adj),
                    "bank_reference": ", ".join(references) or None,
                    "settlement_date": settled_on,
                    "bank_credit_date": bank_date,
                    "difference": money(settled + adj - expected_net) if captured else money(ZERO),
                    "difference_before_adjustments": money(before),
                    "adjustment_effect": adjustment_effect(
                        before=before,
                        adjustment=adj,
                        status_before=status_before,
                        status_after=status,
                        applies=captured,
                    ),
                    **bank_position(
                        expected_net if gross > 0 else ZERO, confirmed, manual, covered
                    ),
                    "unverified_line_count": len(unverified),
                    "unverified_amount": money(sum((dec(u.net_amount) for u in unverified), ZERO)),
                    "unverified_statuses": ", ".join(
                        sorted({u.line_status or "blank" for u in unverified})
                    )
                    or None,
                    "settlement_gap": money((expected_net if gross > 0 else ZERO) - settled),
                    "status": status,
                    "status_label": STATUS_LABELS[status],
                    "payment_recorded": payment_recorded,
                    "line_count": len(lines),
                    "_settlement_days": sorted(
                        {to_ist_date(ln.settlement_date) for ln in lines if ln.settlement_date}
                    ),
                    "age_days": max(
                        (datetime.now(UTC) - (paid_on or order.order_datetime)).days, 0
                    ),
                }
            )
        shared = {
            "evidence": evidence,
            "lines_by_order": lines_by_order,
            "line_bank": line_bank,
        }
        return records, shared

    @staticmethod
    def _apply(records: list[dict], f: Filters, kpi: str | None) -> list[dict]:
        out = records
        if kpi:
            if kpi not in SELECTORS:
                raise ValidationError(f"Unknown KPI selector '{kpi}'.")
            out = [r for r in out if SELECTORS[kpi](r)]
        if f.status:
            out = [r for r in out if r["status"] == f.status]
        if f.age_bucket:
            out = [
                r
                for r in out
                if (
                    (r["status"] == "pending" and r["gross_amount"] > 0)
                    or r["status"] == "partially_reconciled"
                )
                and ageing_bucket(r["age_days"]) == f.age_bucket
            ]
        if f.settled_on:
            out = [r for r in out if f.settled_on in r["_settlement_days"]]
        if f.q and f.q.strip():
            term = f.q.strip().casefold()
            out = [
                r
                for r in out
                if term in r["order_number"].casefold()
                or term == str(r["order_id"]).casefold()
                or term in (r["gateway_transaction_id"] or "").casefold()
                or term in (r["bank_reference"] or "").casefold()
                or term in (r["cashfree_payment_id"] or "").casefold()
                or term in (r["settlement_id"] or "").casefold()
                or term in (r["customer_name"] or "").casefold()
            ]
        return sorted(out, key=lambda r: (r["order_datetime"], str(r["order_id"])), reverse=True)

    async def _unmatched(self, f: Filters, evidence: BankEvidence) -> list[dict]:
        stmt = select(GatewaySettlementLine)
        if f.date_from or f.date_to:
            conds = []
            if f.date_from:
                conds.append(GatewaySettlementLine.settlement_date >= f.date_from)
            if f.date_to:
                conds.append(GatewaySettlementLine.settlement_date <= f.date_to)
            stmt = stmt.where(or_(GatewaySettlementLine.settlement_date.is_(None), and_(*conds)))
        lines = (await self.session.execute(stmt)).scalars().all()
        matched_ids = list({ln.matched_order_id for ln in lines if ln.matched_order_id})
        eligible: set[uuid.UUID] = set()
        for part in chunks(matched_ids):
            eligible.update(
                (
                    await self.session.execute(
                        select(Order.id).where(
                            Order.id.in_(list(part)), Order.payment_type == PaymentType.PREPAID
                        )
                    )
                )
                .scalars()
                .all()
            )
        gateway_filter = f.gateway.strip().casefold() if f.gateway else None
        rows: list[dict] = []
        for ln in lines:
            if ln.matched_order_id is not None and ln.matched_order_id in eligible:
                continue
            if gateway_filter and _gateway_name(ln.gateway).casefold() != gateway_filter:
                continue
            kind = verification_of(ln.line_status)
            successful = kind == "successful"
            reason = (
                "No prepaid order found for this transaction / order id"
                if ln.matched_order_id is None
                else "Matched order is not a prepaid order"
            )
            if not successful:
                why = (
                    "Not a successful settlement" if kind == "unsuccessful" else "Unverified status"
                )
                reason += (
                    f". {why} (status: {ln.line_status or 'blank'}), "
                    "so it adds nothing to the amount"
                )
            rows.append(
                {
                    "counted_amount": money(dec(ln.net_amount)) if successful else money(ZERO),
                    "line_status": ln.line_status,
                    "verification": kind,
                    "kind": "gateway_line",
                    "id": ln.id,
                    "gateway": _gateway_name(ln.gateway),
                    "transaction_id": ln.gateway_transaction_id,
                    "order_ref": ln.order_ref,
                    "reference": ln.settlement_reference,
                    "amount": money(dec(ln.net_amount)),
                    "settlement_id": ln.cf_settlement_id,
                    "cashfree_payment_id": (
                        ln.gateway_transaction_id if ln.source == "api" else None
                    ),
                    "gross_amount": (
                        money(dec(ln.gross_amount)) if ln.gross_amount is not None else None
                    ),
                    "gateway_charges": money(dec(ln.gateway_charges)),
                    "gst_amount": money(dec(ln.gst_amount)),
                    "refund_amount": money(dec(ln.refund_amount)),
                    "date": ln.settlement_date,
                    "reason": reason,
                    "matched_order_id": ln.matched_order_id,
                }
            )
        # Unlinked bank credits belong to no gateway, so a gateway filter must never
        # hide them: an unexplained credit is a financial exception.
        ledger = await load_ledger(self.session)
        for credit in ledger.credits:
            if credit.credit_date and (
                (f.date_from and credit.credit_date < f.date_from)
                or (f.date_to and credit.credit_date > f.date_to)
            ):
                continue
            remaining = ledger.free(credit)
            if remaining <= TOLERANCE:
                continue
            rows.append(
                {
                    "counted_amount": money(remaining),
                    "kind": "bank_credit",
                    "id": credit.id,
                    "gateway": None,
                    "transaction_id": None,
                    "order_ref": None,
                    "reference": credit.reference,
                    "amount": money(remaining),
                    "date": credit.credit_date,
                    "reason": "Bank credit not linked to any remittance or settlement",
                    "matched_order_id": None,
                }
            )
        return rows

    # ---------------------------------------------------------------- views

    async def summary(self, f: Filters) -> dict[str, Any]:
        records, shared = await self._records(f)
        if f.status:  # the status filter narrows every card, percentage and chart too
            records = [r for r in records if r["status"] == f.status]
        unmatched = await self._unmatched(f, shared["evidence"])
        n = len(records)
        paid = [r for r in records if r["gross_amount"] > 0]
        by_status = {s: [r for r in records if r["status"] == s] for s in PREPAID_STATUSES}

        def total(rs: list[dict], key: str) -> Decimal:
            return money(sum((r[key] for r in rs), ZERO))

        gross_total = total(paid, "gross_amount")
        expected_total = total(paid, "expected_net")
        bank_total = total(records, "bank_confirmed_settlement")
        reported_total = total(records, "actual_settlement")
        charges = [r for r in records if SELECTORS["charges"](r)]
        gst = [r for r in records if SELECTORS["gst"](r)]
        refunds = [r for r in records if SELECTORS["refunds"](r)]
        gap_records = [r for r in records if SELECTORS["difference"](r)]
        gap_total = money(expected_total - reported_total)
        unmatched_lines = [u for u in unmatched if u["kind"] == "gateway_line"]
        unmatched_credits = [u for u in unmatched if u["kind"] == "bank_credit"]
        lines_in_dataset = sum(r["line_count"] for r in records)
        tx_total = lines_in_dataset + len(unmatched_lines)
        matched = by_status["matched"]
        pending = by_status["pending"]
        pending_paid = [r for r in pending if r["gross_amount"] > 0]
        kpis = {
            "total_prepaid_orders": {"count": n, "amount": total(records, "order_amount")},
            "gross_collection": {"count": len(paid), "amount": gross_total},
            "gateway_charges": {"count": len(charges), "amount": total(records, "gateway_charges")},
            "gst": {"count": len(gst), "amount": total(records, "gst_amount")},
            "refunds": {"count": len(refunds), "amount": total(records, "refund_amount")},
            "expected_net": {"count": len(paid), "amount": expected_total},
            "bank_settlement": {
                "count": len([r for r in records if SELECTORS["bank_settlement"](r)]),
                "amount": bank_total,
            },
            "reported_settlement": {
                "count": len([r for r in records if SELECTORS["reported_settlement"](r)]),
                "amount": reported_total,
            },
            "settlement_difference": {"count": len(gap_records), "amount": gap_total},
            "unmatched": {
                "count": len(unmatched_lines),
                "amount": money(sum((u["counted_amount"] for u in unmatched_lines), ZERO)),
            },
            "unmatched_bank_credits": {
                "count": len(unmatched_credits),
                "amount": money(sum((u["counted_amount"] for u in unmatched_credits), ZERO)),
            },
            "reconciled": {"count": len(matched), "amount": total(matched, "expected_net")},
            "pending": {"count": len(pending_paid), "amount": total(pending_paid, "gross_amount")},
            "failed": {
                "count": len(by_status["failed"]),
                "amount": total(by_status["failed"], "order_amount"),
            },
            "refunded": {
                "count": len(by_status["refunded"]),
                "amount": total(by_status["refunded"], "refund_amount"),
            },
            "short": {
                "count": len(by_status["short"]),
                "amount": money(sum((-r["difference"] for r in by_status["short"]), ZERO)),
            },
            "excess": {
                "count": len(by_status["excess"]),
                "amount": money(sum((r["difference"] for r in by_status["excess"]), ZERO)),
            },
        }
        pct = [
            percentage_item(
                "completion",
                "Reconciliation completion",
                len(matched),
                len(paid),
                kind="count",
                numerator_label="fully reconciled orders",
                denominator_label="prepaid orders with a captured payment",
                drill="reconciled",
            ),
            percentage_item(
                "completion_value",
                "Reconciliation completion (value)",
                kpis["reconciled"]["amount"],
                expected_total,
                kind="value",
                numerator_label="expected net of fully reconciled orders",
                denominator_label="expected net settlement",
                drill="reconciled",
            ),
            percentage_item(
                "pending",
                "Pending settlement",
                len(pending_paid),
                len(paid),
                kind="count",
                numerator_label="paid orders with no gateway-reported settlement",
                denominator_label="prepaid orders with a captured payment",
                drill="pending",
            ),
            percentage_item(
                "pending_value",
                "Pending settlement (value)",
                kpis["pending"]["amount"],
                gross_total,
                kind="value",
                numerator_label="gross collection with no gateway-reported settlement",
                denominator_label="gross prepaid collection",
                drill="pending",
            ),
            percentage_item(
                "matched_tx",
                "Matched transactions",
                lines_in_dataset,
                tx_total,
                kind="count",
                numerator_label="settlement lines matched to prepaid orders",
                denominator_label="settlement lines in this view",
                drill="reported_settlement",
            ),
            percentage_item(
                "unmatched_tx",
                "Unmatched transactions",
                len(unmatched_lines),
                tx_total,
                kind="count",
                numerator_label="unmatched settlement lines",
                denominator_label="settlement lines in this view",
                drill="unmatched",
            ),
            percentage_item(
                "difference",
                "Settlement difference (value)",
                abs(gap_total),
                expected_total,
                kind="value",
                numerator_label="expected net minus settlement received (gateway-reported)",
                denominator_label="expected net settlement",
                drill="difference",
            ),
            percentage_item(
                "charges",
                "Gateway charges (value)",
                kpis["gateway_charges"]["amount"],
                gross_total,
                kind="value",
                numerator_label="gateway charges",
                denominator_label="gross prepaid collection",
                drill="charges",
            ),
            percentage_item(
                "refunds",
                "Refunds (value)",
                kpis["refunds"]["amount"],
                gross_total,
                kind="value",
                numerator_label="refunds",
                denominator_label="gross prepaid collection",
                drill="refunds",
            ),
        ]
        extra = bank_and_adjustment_kpis(records, "bank_confirmed_settlement")
        kpis["bank_settlement"]["statement_matched"] = extra["bank_position"]["statement_matched"]
        kpis["bank_settlement"]["manually_verified"] = extra["bank_position"]["manually_verified"]
        kpis.update(extra)
        adjustments = extra["adjusted"]["amount"]
        # The headline never includes adjustments; their effect is shown beside it.
        kpis["settlement_difference"]["adjustments"] = adjustments
        kpis["settlement_difference"]["after_adjustments"] = money(gap_total - adjustments)
        with_unverified = [r for r in records if SELECTORS["unverified"](r)]
        stray_unverified = [u for u in unmatched_lines if u["verification"] == "unverified"]
        kpis["unverified"] = {
            "count": len(with_unverified),
            "amount": total(with_unverified, "unverified_amount"),
            "unmatched_lines": len(stray_unverified),
            "unmatched_amount": money(sum((u["amount"] for u in stray_unverified), ZERO)),
        }
        return {
            "kpis": kpis,
            "percentages": pct,
            "status_breakdown": [
                {
                    "status": s,
                    "label": STATUS_LABELS[s],
                    "count": len(by_status[s]),
                    "amount": total(by_status[s], "gross_amount"),
                    "percent_of_orders": (len(by_status[s]) / n * 100) if n else None,
                }
                for s in PREPAID_STATUSES
            ],
            "gateways": self._gateway_rows(records, unmatched_lines),
            "denominators": {
                "prepaid_orders": n,
                "paid_orders": len(paid),
                "gross_collection": gross_total,
                "expected_net": expected_total,
                "settlement_lines": tx_total,
            },
        }

    def _gateway_rows(self, records: list[dict], unmatched_lines: list[dict]) -> list[dict]:
        groups: dict[str, list[dict]] = defaultdict(list)
        for r in records:
            groups[r["gateway"]].append(r)
        stray: dict[str, int] = defaultdict(int)
        for u in unmatched_lines:
            stray[u["gateway"]] += 1
        out = []
        for gateway in sorted(set(groups) | set(stray)):
            rs = groups.get(gateway, [])
            paid = [r for r in rs if r["gross_amount"] > 0]
            matched = [r for r in rs if r["status"] == "matched"]
            out.append(
                {
                    "gateway": gateway,
                    "orders": len(rs),
                    "gross_amount": money(sum((r["gross_amount"] for r in rs), ZERO)),
                    "gateway_charges": money(sum((r["gateway_charges"] for r in rs), ZERO)),
                    "gst_amount": money(sum((r["gst_amount"] for r in rs), ZERO)),
                    "refund_amount": money(sum((r["refund_amount"] for r in rs), ZERO)),
                    "expected_net": money(sum((r["expected_net"] for r in paid), ZERO)),
                    "reported_settlement": money(sum((r["actual_settlement"] for r in rs), ZERO)),
                    "bank_settlement": money(
                        sum((r["bank_confirmed_settlement"] for r in rs), ZERO)
                    ),
                    "pending_orders": sum(1 for r in paid if r["status"] == "pending"),
                    "matched_orders": len(matched),
                    "unmatched_lines": stray.get(gateway, 0),
                    "completion_percent": (len(matched) / len(paid) * 100) if paid else None,
                }
            )
        out.sort(key=lambda g: -g["orders"])
        return out

    async def drilldown_records(self, f: Filters, kpi: str | None) -> tuple[list[dict], dict]:
        """Every record behind a card (all pages), each with the amount it
        contributes, plus totals with and without the search term."""
        records, _shared = await self._records(f)
        label, value = MEASURES.get(kpi or "", GROSS_MEASURE)
        found = self._apply(records, f, kpi)
        base = self._apply(records, replace(f, q=None), kpi) if f.q else found
        rows = [{**r, "contribution": money(value(r))} for r in found]
        totals = drill_totals(label, rows, [money(value(r)) for r in base])
        return rows, totals

    async def records(
        self, f: Filters, *, kpi: str | None, page_params: PageParams
    ) -> tuple[list[dict], int, dict]:
        rows, totals = await self.drilldown_records(f, kpi)
        page, total = paginate(rows, page_params)
        return page, total, totals

    async def drilldown_unmatched(self, f: Filters, kind: str | None) -> tuple[list[dict], dict]:
        evidence = await load_bank_evidence(self.session, MODULE)
        everything = await self._unmatched(f, evidence)
        if kind in ("gateway_line", "bank_credit"):
            everything = [r for r in everything if r["kind"] == kind]
        rows = everything
        if f.q and f.q.strip():
            term = f.q.strip().casefold()
            rows = [
                r
                for r in rows
                if any(
                    term in (r.get(key) or "").casefold()
                    for key in ("transaction_id", "order_ref", "reference", "settlement_id")
                )
            ]
        rows = sorted(
            rows,
            key=lambda r: (r["date"] is None, r["date"] or datetime.min.replace(tzinfo=UTC)),
            reverse=True,
        )
        rows = [{**r, "contribution": r["counted_amount"]} for r in rows]
        totals = drill_totals(
            {
                "gateway_line": "Unmatched gateway-reported amount",
                "bank_credit": "Unlinked bank credit amount",
            }.get(kind or "", "Mixed list: see the separate totals below"),
            rows,
            [r["counted_amount"] for r in everything],
            kinds={
                key: label
                for key, label in (
                    ("gateway_line", "Settlement lines"),
                    ("bank_credit", "Bank credits"),
                )
                if kind in (None, "", key)
            },
        )
        return rows, totals

    async def unmatched(
        self, f: Filters, *, kind: str | None, page_params: PageParams
    ) -> tuple[list[dict], int, dict]:
        rows, totals = await self.drilldown_unmatched(f, kind)
        page, total = paginate(rows, page_params)
        return page, total, totals

    async def order_detail(self, order_id: uuid.UUID) -> dict[str, Any]:
        """One order's reconciliation record (same computation as the
        dashboard) and the evidence behind it. No raw gateway payloads."""
        records, _shared = await self._records(Filters(order_id=order_id))
        if not records:
            raise NotFoundError("This order is not part of prepaid reconciliation.")
        record = records[0]
        found = (
            (
                await self.session.execute(
                    select(GatewaySettlementLine)
                    .where(GatewaySettlementLine.matched_order_id == order_id)
                    .order_by(GatewaySettlementLine.created_at)
                )
            )
            .scalars()
            .all()
        )
        lines = [
            {
                "id": ln.id,
                "source": ln.source,
                "gateway": _gateway_name(ln.gateway),
                "event_type": ln.event_type,
                "transaction_id": ln.gateway_transaction_id,
                "order_ref": ln.order_ref,
                "settlement_id": ln.cf_settlement_id,
                "reference": ln.settlement_reference,
                "gross_amount": (
                    money(dec(ln.gross_amount)) if ln.gross_amount is not None else None
                ),
                "gateway_charges": money(dec(ln.gateway_charges)),
                "gst_amount": money(dec(ln.gst_amount)),
                "refund_amount": money(dec(ln.refund_amount)),
                "amount": money(dec(ln.net_amount)),
                "date": ln.settlement_date,
                "line_status": ln.line_status,
                # Same rule the calculation uses: only recognised success counts.
                "counted": is_successful(ln.line_status),
                "verification": verification_of(ln.line_status),
                "verification_label": VERIFICATION_LABELS[verification_of(ln.line_status)],
                "match_method": ln.match_method,
            }
            for ln in found
        ]
        payments = (
            (
                await self.session.execute(
                    select(Payment).where(Payment.order_id == order_id).order_by(Payment.created_at)
                )
            )
            .scalars()
            .all()
        )
        refunds = (
            (await self.session.execute(select(Refund).where(Refund.order_id == order_id)))
            .scalars()
            .all()
        )
        references = [ln["reference"] for ln in lines if ln["counted"]]
        return {
            "module": MODULE,
            "record": {k: v for k, v in record.items() if not k.startswith("_")},
            "lines": lines,
            "payments": [
                {
                    "provider": p.provider,
                    "transaction_id": p.external_transaction_id,
                    "status": _enum_value(p.status),
                    "amount": money(dec(p.amount)),
                    "paid_at": p.paid_at,
                }
                for p in payments
            ],
            "refunds": [
                {"status": _enum_value(r.status), "amount": money(dec(r.amount))} for r in refunds
            ],
            "bank_links": await bank_links_for(self.session, MODULE, references),
            "bank_references": await reference_positions(self.session, MODULE, references),
            "adjustments": await adjustments_for(self.session, MODULE, order_id),
        }

    async def export_drilldown_xlsx(
        self, f: Filters, *, kpi: str | None, unmatched: bool, kind: str | None
    ) -> bytes:
        def num(value: Any) -> float | None:
            return float(value) if value is not None else None

        if unmatched:
            rows, totals = await self.drilldown_unmatched(f, kind)
            columns = [
                ("Type", lambda r: r["kind"].replace("_", " ")),
                ("Gateway", lambda r: r["gateway"]),
                ("Cashfree payment ID", lambda r: r.get("cashfree_payment_id")),
                ("Transaction ID", lambda r: r["transaction_id"]),
                ("Order reference", lambda r: r["order_ref"]),
                ("Settlement ID", lambda r: r.get("settlement_id")),
                ("UTR / reference", lambda r: r["reference"]),
                ("Settlement / credit date", lambda r: fmt_dt(r["date"])),
                ("Gross amount", lambda r: num(r.get("gross_amount"))),
                ("Charges", lambda r: num(r.get("gateway_charges"))),
                ("GST", lambda r: num(r.get("gst_amount"))),
                ("Refunds", lambda r: num(r.get("refund_amount"))),
                ("Net amount", lambda r: num(r["amount"])),
                ("Status", lambda r: r.get("line_status")),
                ("Verification", lambda r: VERIFICATION_LABELS.get(r.get("verification"))),
                ("Counts toward total", lambda r: num(r["contribution"])),
                ("Why unmatched", lambda r: r["reason"]),
            ]
        else:
            rows, totals = await self.drilldown_records(f, kpi)
            columns = [
                ("Order number", lambda r: r["order_number"]),
                ("Customer", lambda r: r["customer_name"]),
                ("Order date", lambda r: fmt_dt(r["order_datetime"])),
                ("Payment status", lambda r: r["payment_status"]),
                ("Fulfilment status", lambda r: r["delivery_status"]),
                ("Gateway", lambda r: r["gateway"]),
                ("Cashfree payment ID", lambda r: r["cashfree_payment_id"]),
                ("Transaction ID", lambda r: r["gateway_transaction_id"]),
                ("Settlement ID", lambda r: r["settlement_id"]),
                ("UTR / reference", lambda r: r["bank_reference"]),
                ("Order amount", lambda r: num(r["order_amount"])),
                ("Gross amount", lambda r: num(r["gross_amount"])),
                ("Charges", lambda r: num(r["gateway_charges"])),
                ("GST", lambda r: num(r["gst_amount"])),
                ("Refunds", lambda r: num(r["refund_amount"])),
                ("Expected net", lambda r: num(r["expected_net"])),
                ("Settlement received (gateway-reported)", lambda r: num(r["actual_settlement"])),
                ("Prepaid bank confirmed", lambda r: num(r["bank_confirmed_settlement"])),
                (
                    "Settlement difference (expected net - settlement received)",
                    lambda r: num(r["settlement_gap"]),
                ),
                (
                    "Status difference (received + adjustments - expected net)",
                    lambda r: num(r["difference"]),
                ),
                ("Settlement date", lambda r: fmt_dt(r["settlement_date"])),
                ("Bank credit date", lambda r: fmt_dt(r["bank_credit_date"])),
                ("Reconciliation status", lambda r: r["status_label"]),
                *(
                    (header, lambda r, key=key: export_value(r[key]))
                    for header, key in BANK_EXPORT_COLUMNS
                ),
                ("Unverified amount (not counted)", lambda r: num(r["unverified_amount"])),
                ("Unverified statuses", lambda r: r["unverified_statuses"]),
                (f"Counts toward card: {totals['measure']}", lambda r: num(r["contribution"])),
            ]
        return drilldown_workbook(columns, rows, totals, module=MODULE)

    async def report_gateways(self, f: Filters) -> list[dict]:
        records, shared = await self._records(f)
        unmatched = await self._unmatched(f, shared["evidence"])
        return self._gateway_rows(
            self._apply(records, f, None), [u for u in unmatched if u["kind"] == "gateway_line"]
        )

    async def report_settlement_dates(self, f: Filters) -> list[dict]:
        records, shared = await self._records(f)
        records = self._apply(records, f, None)
        evidence: BankEvidence = shared["evidence"]
        buckets: dict[str, dict] = {}

        def bucket(day: str) -> dict:
            return buckets.setdefault(
                day,
                {
                    "date": day,
                    "lines": 0,
                    "net_amount": ZERO,
                    "gateway_charges": ZERO,
                    "gst_amount": ZERO,
                    "bank_confirmed_amount": ZERO,
                    "unmatched_lines": 0,
                },
            )

        for order_id in {r["order_id"] for r in records}:
            for ln in shared["lines_by_order"].get(order_id, []):
                b = bucket(to_ist_date(ln.settlement_date) if ln.settlement_date else "No date")
                b["lines"] += 1
                b["net_amount"] += dec(ln.net_amount)
                b["gateway_charges"] += dec(ln.gateway_charges)
                b["gst_amount"] += dec(ln.gst_amount)
                if ln.id in shared["line_bank"]:
                    b["bank_confirmed_amount"] += shared["line_bank"][ln.id].confirmed
        for u in await self._unmatched(f, evidence):
            if u["kind"] != "gateway_line":
                continue
            b = bucket(to_ist_date(u["date"]) if u["date"] else "No date")
            b["lines"] += 1
            b["unmatched_lines"] += 1
            b["net_amount"] += u["counted_amount"]
        out = [
            {**b, **{k: money(v) for k, v in b.items() if isinstance(v, Decimal)}}
            for b in buckets.values()
        ]
        return sorted(out, key=lambda b: b["date"], reverse=True)

    async def report_ageing(self, f: Filters) -> list[dict]:
        records, _shared = await self._records(f)
        records = self._apply(records, f, None)
        rows = {
            label: {
                "bucket": label,
                "pending_orders": 0,
                "pending_amount": ZERO,
                "awaiting_bank_orders": 0,
                "awaiting_bank_amount": ZERO,
            }
            for label, _lo, _hi in AGEING_BUCKETS
        }
        for r in records:
            row = rows[ageing_bucket(r["age_days"])]
            if r["status"] == "pending" and r["gross_amount"] > 0:
                row["pending_orders"] += 1
                row["pending_amount"] += r["gross_amount"]  # same as the card
            elif r["status"] == "partially_reconciled":
                row["awaiting_bank_orders"] += 1
                row["awaiting_bank_amount"] += (
                    r["actual_settlement"] - r["bank_confirmed_settlement"]
                )
        return [
            {**row, **{k: money(v) for k, v in row.items() if isinstance(v, Decimal)}}
            for row in rows.values()
        ]

    async def report_settlement_lots(
        self, f: Filters, page_params: PageParams
    ) -> tuple[list[dict], int]:
        """Cashfree settlement lots already synced by the existing integration
        (API), with whether a bank credit has been linked to each UTR."""
        lots = (await self.session.execute(select(CashfreeSettlement))).scalars().all()
        evidence = await load_bank_evidence(self.session, MODULE)
        rows = []
        for lot in lots:
            when = lot.settlement_processed_on
            if when and ((f.date_from and when < f.date_from) or (f.date_to and when > f.date_to)):
                continue
            ref = norm_ref(lot.settlement_utr)
            settled = dec(lot.amount_settled)
            credited = evidence.credited.get(ref, ZERO) if ref else ZERO
            rows.append(
                {
                    "gateway": "Cashfree",
                    "settlement_id": lot.cf_settlement_id,
                    "utr": lot.settlement_utr,
                    "status": lot.status,
                    "processed_on": when,
                    "payment_amount": money(dec(lot.payment_amount)),
                    "charges": money(dec(lot.pg_service_charge)),
                    "gst": money(dec(lot.pg_service_tax)),
                    "amount_settled": money(settled),
                    "bank_credited": money(credited),
                    "bank_state": "linked"
                    if ref and credited > 0 and credited >= settled - TOLERANCE and settled > 0
                    else ("partially_linked" if credited > 0 else "unlinked"),
                }
            )
        floor = datetime.min.replace(tzinfo=UTC)
        rows.sort(key=lambda r: r["processed_on"] or floor, reverse=True)
        return paginate(rows, page_params)

    async def export_xlsx(self, f: Filters) -> bytes:
        records, shared = await self._records(f)
        records = self._apply(records, f, None)
        wb = Workbook()
        ws = wb.active
        ws.title = "Order-wise"
        ws.append(
            [
                "Order ID",
                "Customer",
                "Gateway",
                "Gateway transaction ID",
                "Transaction date",
                "Gross amount",
                "Gateway charges",
                "GST",
                "Refund",
                "Expected net settlement",
                "Settlement received (gateway-reported)",
                "Prepaid bank confirmed",
                "Bank reference",
                "Settlement date",
                "Bank credit date",
                "Settlement difference (expected net - settlement received)",
                "Status difference (received + adjustments - expected net)",
                "Status",
                *(header for header, _key in BANK_EXPORT_COLUMNS),
                "Unverified amount (not counted)",
                "Unverified statuses",
            ]
        )
        for r in records:
            ws.append(
                [
                    r["order_number"],
                    r["customer_name"],
                    r["gateway"],
                    r["gateway_transaction_id"],
                    fmt_dt(r["transaction_date"]),
                    float(r["gross_amount"]),
                    float(r["gateway_charges"]),
                    float(r["gst_amount"]),
                    float(r["refund_amount"]),
                    float(r["expected_net"]),
                    float(r["actual_settlement"]),
                    float(r["bank_confirmed_settlement"]),
                    r["bank_reference"],
                    fmt_dt(r["settlement_date"]),
                    fmt_dt(r["bank_credit_date"]),
                    float(r["settlement_gap"]),
                    float(r["difference"]),
                    r["status_label"],
                    *(export_value(r[key]) for _header, key in BANK_EXPORT_COLUMNS),
                    float(r["unverified_amount"]),
                    r["unverified_statuses"],
                ]
            )
        gw = wb.create_sheet("Gateway-wise")
        gw.append(
            [
                "Gateway",
                "Orders",
                "Gross",
                "Charges",
                "GST",
                "Refunds",
                "Expected net",
                "Settlement received (gateway-reported)",
                "Prepaid bank confirmed",
                "Pending",
                "Matched",
                "Unmatched lines",
                "Completion %",
            ]
        )
        for g in await self.report_gateways(f):
            gw.append(
                [
                    g["gateway"],
                    g["orders"],
                    float(g["gross_amount"]),
                    float(g["gateway_charges"]),
                    float(g["gst_amount"]),
                    float(g["refund_amount"]),
                    float(g["expected_net"]),
                    float(g["reported_settlement"]),
                    float(g["bank_settlement"]),
                    g["pending_orders"],
                    g["matched_orders"],
                    g["unmatched_lines"],
                    round_pct(g["completion_percent"]),
                ]
            )
        sd = wb.create_sheet("Settlement-date-wise")
        sd.append(
            [
                "Date",
                "Lines",
                "Gateway-reported net settlement (incl. unmatched lines)",
                "Charges",
                "GST",
                "Prepaid bank confirmed",
                "Unmatched lines",
            ]
        )
        for b in await self.report_settlement_dates(f):
            sd.append(
                [
                    b["date"],
                    b["lines"],
                    float(b["net_amount"]),
                    float(b["gateway_charges"]),
                    float(b["gst_amount"]),
                    float(b["bank_confirmed_amount"]),
                    b["unmatched_lines"],
                ]
            )
        ag = wb.create_sheet("Settlement ageing")
        ag.append(
            [
                "Age",
                "Pending orders",
                "Pending amount (gross collected)",
                "Awaiting bank orders",
                "Settlement received awaiting bank confirmation",
            ]
        )
        for b in await self.report_ageing(f):
            ag.append(
                [
                    b["bucket"],
                    b["pending_orders"],
                    float(b["pending_amount"]),
                    b["awaiting_bank_orders"],
                    float(b["awaiting_bank_amount"]),
                ]
            )
        for title, status in (
            ("Failed transactions", "failed"),
            ("Refunded transactions", "refunded"),
        ):
            sheet = wb.create_sheet(title)
            sheet.append(["Order ID", "Customer", "Gateway", "Gross", "Refund", "Status"])
            for r in records:
                if r["status"] == status:
                    sheet.append(
                        [
                            r["order_number"],
                            r["customer_name"],
                            r["gateway"],
                            float(r["gross_amount"]),
                            float(r["refund_amount"]),
                            r["status_label"],
                        ]
                    )
        un = wb.create_sheet("Unmatched")
        un.append(
            [
                "Type",
                "Gateway",
                "Transaction ID",
                "Order ref",
                "Reference",
                "Amount",
                "Counts toward unmatched total",
                "Date",
                "Reason",
            ]
        )
        for u in await self._unmatched(f, shared["evidence"]):
            un.append(
                [
                    "Gateway line" if u["kind"] == "gateway_line" else "Bank credit",
                    u["gateway"],
                    u["transaction_id"],
                    u["order_ref"],
                    u["reference"],
                    float(u["amount"]),
                    float(u["counted_amount"]),
                    fmt_dt(u["date"]),
                    u["reason"],
                ]
            )
        kpis = (await self.summary(replace(f, q=None)))["kpis"]
        difference = kpis["settlement_difference"]
        add_bank_summary_sheet(
            wb,
            kpis,
            (
                (
                    "Settlement Difference (expected net - Total Settlement Received)",
                    difference["amount"],
                ),
                (
                    "Settlement difference less manual adjustments",
                    difference["after_adjustments"],
                ),
                ("Unverified amount on orders (not counted)", kpis["unverified"]["amount"]),
                (
                    "Unverified unmatched amount (not counted)",
                    kpis["unverified"]["unmatched_amount"],
                ),
            ),
        )
        add_definitions_sheet(wb, MODULE)
        neutralise_formulas(wb)
        buffer = BytesIO()
        wb.save(buffer)
        return buffer.getvalue()


def _enum_value(value: Any) -> str | None:
    return getattr(value, "value", value) if value is not None else None

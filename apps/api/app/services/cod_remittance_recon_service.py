"""Module 6 -- COD Remittance Reconciliation.

Reconciles COD collections reported by shipping partners against delivered
COD orders and actual bank credits.

Dataset: delivered COD orders (unified status `delivered`, payment type
COD) placed in the selected date range, optionally narrowed to one carrier
family. Expected COD = the order total. Evidence:

- remittance lines (an uploaded partner settlement report), matched to an
  order by AWB or order reference, or manually by a permitted user; if an
  order has no uploaded line, the Shiprocket-reported remitted amount from
  the existing `cod_remittances` table (API sync) is used instead -- the two
  sources are never added together, so nothing is double counted;
- bank credits linked to the remittance reference ("COD bank confirmed" /
  bank credit date) -- kept separate from the courier-reported "COD settled";
- reasoned manual adjustments.

Nothing is assumed: a delivered order with no remittance is `pending`.
Every KPI, percentage, chart value and table row is computed from the same
list of records, so a drilldown always equals the number that opened it.
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
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import NotFoundError, ValidationError
from app.models.auth import User
from app.models.cod_remittance import CodRemittance
from app.models.enums import OrderStatus, PaymentType
from app.models.order import Order
from app.models.remittance_reconciliation import (
    BankCredit,
    BankCreditLink,
    CodRemittanceLine,
    ReconciliationAdjustment,
    SettlementFile,
)
from app.models.shipment import Shipment
from app.models.shiprocket_finance import ShiprocketShipmentCharge
from app.repositories.order import OrderRepository
from app.repositories.shipment_effective import (
    effective_courier_name,
    effective_delivery_date,
    partner_display_name,
)
from app.schemas.common import PageParams
from app.services import remittance_upload_parser as parser
from app.services.remittance_common import (
    AGEING_BUCKETS,
    BANK_EXPORT_COLUMNS,
    BANK_MEASURES,
    BANK_SELECTORS,
    STATUS_LABELS,
    TOLERANCE,
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
    cod_status,
    dec,
    drill_totals,
    drilldown_workbook,
    evidence_from,
    export_value,
    fmt_dt,
    link_row,
    load_bank_evidence,
    load_ledger,
    money,
    neutralise_formulas,
    percentage_item,
    reference_lines,
    reference_positions,
    store_upload,
    to_ist_date,
)

MODULE = "cod"
COD_STATUSES = ("pending", "partially_reconciled", "matched", "short", "excess")

# The payment-status view groups the same `cod_status()` outcomes into 4
# buckets instead of showing all 5 individually:
#   - payment_success:   "matched"   -- fully remitted AND bank-confirmed.
#   - payment_initiated: "partially_reconciled" / "short" / "excess" -- the
#     courier has reported *something* (a remittance exists), but it is not
#     yet a clean, bank-confirmed match.
#   - payment_failed:    a confirmed `"failed"` CRF entry for the order's
#     AWB -- Shiprocket explicitly reported the payout attempt FAILED.
#     Checked and excluded BEFORE Payment Pending, per Shiprocket Support's
#     own instruction: a failure must never be counted as pending (“no
#     data yet”) or as successful.
#   - payment_pending:   "pending"   -- no courier-reported remittance at
#     all, AND not a confirmed failure.
# This is a derived grouping of the OMS's own computed reconciliation status
# (`cod_status()`), not a parse of Shiprocket's raw remittance-status text --
# that field's real vocabulary has never been confirmed against a live
# account (see `app/integrations/shiprocket/remittance.py`), so it is not
# used for this classification.
#
# One addition, from Shiprocket's DOCUMENTED order-detail response
# (`GET /orders/show/{id}`: `remittance_utr`, `remittance_date`, and the
# free-text `cod_recieved_hover_text`): an order whose status is still
# "pending" because no remittance AMOUNT is known, but for which Shiprocket
# reports a payout reference or a date, is `payout_reported` and counts as
# Payment Initiated, never Success. Both are treated identically -- a
# presence check only, never a parse of the hover text's wording (that
# vocabulary is not documented). Shiprocket documents no remitted amount,
# CRF id, or bank settlement anywhere on this endpoint, so neither can ever
# establish "success" by themselves; the order keeps status "pending" and
# every amount stays as it was -- only `payout_reported` flips to `True`.
#
# A second source supersedes the above when it applies: the CRF feed,
# consumed by `CodReconciliationService.sync_remittances` via
# `ShiprocketAdapter.fetch_cod_remittances` -- CONFIRMED DIRECTLY BY
# SHIPROCKET SUPPORT as `POST https://api.shiprocket.in/v1/cod/remittance`
# (two earlier attempts were wrong: `GET .../account/details/remittance`
# 500'd; `GET .../v1/cod/remittance` joined onto the wrong base and 404'd).
# Its `status` is a real, three-value enum (`pending` / `processed` /
# `failed`), not a presence check:
#   - `processed` entries are stored as `CodRemittanceLine` rows (summed
#     per order, deduped by a `crf_id` if one is ever present, else a
#     deterministic (awb, amount, status) fingerprint -- see
#     `_cod_entry_dedupe_key`) and flow through the SAME `lines` branch
#     above as an uploaded settlement report -- i.e. they become
#     `remitted` evidence, so they still need a matched bank credit to
#     reach Payment Success (`matched`); without one they land on
#     `partially_reconciled`, i.e. Payment Initiated. This keeps the one
#     hard rule in this module intact: Shiprocket's own word that money
#     moved is never, by itself, "bank confirmed".
#   - `pending` entries are stored on the `CodRemittance` summary row but
#     deliberately do NOT set `payout_reported`: a real courier-reported
#     "not yet" is still Payment Pending, just a *known* pending rather
#     than *no data at all* (see `_data_coverage`'s
#     `order_payout_references`, which does count it).
#   - `failed` entries are stored the same way, with `remittance_status`
#     preserved verbatim as `"failed"` -- drives the dedicated
#     `payment_failed` bucket below, and is never downgraded back to
#     `pending` by a later sync (see `sync_remittances`).
PAYMENT_INITIATED_STATUSES = ("partially_reconciled", "short", "excess")


def _is_payment_success(r: dict) -> bool:
    """Only bank-confirmed reconciliation. `matched` also covers an order settled
    purely by a manual adjustment (no courier remittance, no bank credit); that is
    a write-off, not a payment, so it needs actual bank-confirmed money behind it."""
    return r["status"] == "matched" and r["amount_received"] > 0


def _is_payment_initiated(r: dict) -> bool:
    return r["status"] in PAYMENT_INITIATED_STATUSES or bool(r.get("payout_reported"))


def _is_payment_failed(r: dict) -> bool:
    """A confirmed Shiprocket `"failed"` CRF entry for this order's AWB.
    Checked before Payment Pending so a failure is never silently counted
    as "no data yet" -- see `payout_failed` in `_records()`."""
    return bool(r.get("payout_failed"))


def _is_payment_pending(r: dict) -> bool:
    """Everything that is neither Success, Initiated, nor Failed, so the
    four always partition the delivered orders: no courier remittance or
    payout reported (including an adjustment-only `matched` order with no
    bank-confirmed money), and no confirmed failure either."""
    return (
        not _is_payment_success(r)
        and not _is_payment_initiated(r)
        and not _is_payment_failed(r)
    )


# KPI / percentage drill selector -> predicate over a record.
SELECTORS = {
    "delivered": lambda r: True,
    "expected": lambda r: True,
    "received": lambda r: r["remittance_amount"] != 0,
    "bank_confirmed": lambda r: r["amount_received"] != 0,
    "pending": lambda r: r["status"] == "pending",
    "short": lambda r: r["status"] == "short",
    "excess": lambda r: r["status"] == "excess",
    "reconciled": lambda r: r["status"] == "matched",
    "partial": lambda r: r["status"] == "partially_reconciled",
    "payment_success": _is_payment_success,
    "payment_initiated": _is_payment_initiated,
    "payment_failed": _is_payment_failed,
    "payment_pending": _is_payment_pending,
    **BANK_SELECTORS,
}

# What ONE record contributes to the card that opened a drilldown. Summed over
# every record of the selection it equals the card's KPI (see the tests).
EXPECTED_MEASURE = ("Expected COD", lambda r: r["expected_amount"])
MEASURES = {
    "received": ("COD settled (courier-reported)", lambda r: r["remittance_amount"]),
    "bank_confirmed": ("COD bank confirmed", lambda r: r["amount_received"]),
    "pending": ("Pending COD", lambda r: r["expected_amount"] + r["adjustment_amount"]),
    "short": ("Short amount", lambda r: -r["difference"]),
    "excess": ("Excess amount", lambda r: r["difference"]),
    "payment_success": ("Payment success (bank-confirmed COD)", lambda r: r["expected_amount"]),
    "payment_initiated": (
        "Payment initiated (reported, not yet bank-confirmed)",
        lambda r: r["expected_amount"],
    ),
    "payment_failed": (
        "Payment failed (Shiprocket reported the payout attempt failed)",
        lambda r: r["expected_amount"],
    ),
    "payment_pending": (
        "Payment pending (no remittance reported yet)",
        lambda r: r["expected_amount"],
    ),
    **BANK_MEASURES,
}


@dataclass
class Filters:
    date_from: datetime | None = None
    date_to: datetime | None = None
    partner: str | None = None
    status: str | None = None
    q: str | None = None
    age_bucket: str | None = None
    settled_on: str | None = None
    order_id: uuid.UUID | None = None


def _paginate(items: list, page_params: PageParams) -> tuple[list, int]:
    start = page_params.offset
    return items[start : start + page_params.page_size], len(items)


def _kpi(records: list[dict], key: str = "expected_amount") -> dict[str, Any]:
    return {"count": len(records), "amount": money(sum((r[key] for r in records), ZERO))}


class CodRemittanceReconciliationService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.orders = OrderRepository(session)

    # ------------------------------------------------------------ ingestion

    async def import_report(
        self,
        *,
        partner: str,
        filename: str,
        content_type: str | None,
        data: bytes,
        user: User | None,
        column_map: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        if not partner.strip():
            raise ValidationError("Shipping partner name is required.")
        parsed = parser.parse_cod_remittances(filename, data, partner, column_map)
        record = await store_upload(
            self.session,
            module=MODULE,
            source_name=partner,
            filename=filename,
            content_type=content_type,
            data=data,
            user=user,
        )
        existing = (
            set(
                (
                    await self.session.execute(
                        select(CodRemittanceLine.dedupe_key).where(
                            CodRemittanceLine.dedupe_key.in_([r["dedupe_key"] for r in parsed.rows])
                        )
                    )
                )
                .scalars()
                .all()
            )
            if parsed.rows
            else set()
        )
        added: list[CodRemittanceLine] = []
        duplicates = parsed.skipped
        for row in parsed.rows:
            if row["dedupe_key"] in existing:
                duplicates += 1
                continue
            line = CodRemittanceLine(file_id=record.id, partner_name=partner.strip()[:128], **row)
            self.session.add(line)
            added.append(line)
        await self.session.flush()
        matched = await self._auto_match(added)
        linked = await auto_link_bank_credits(
            self.session,
            MODULE,
            (ln.remittance_reference for ln in added),
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
            "remittance.cod.upload",
            "settlement_file",
            record.id,
            new={"file": filename, "partner": partner, "accepted": len(added)},
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
            "row_errors": parsed.errors[:50],
            "column_mapping": parsed.column_mapping,
            "ignored_columns": parsed.ignored_columns,
        }

    async def _auto_match(self, lines: list[CodRemittanceLine]) -> int:
        """Match by AWB first, then order reference. Never fuzzy."""
        todo = [ln for ln in lines if ln.matched_order_id is None]
        if not todo:
            return 0
        awb_map: dict[str, uuid.UUID] = {}
        for part in chunks(sorted({ln.awb for ln in todo if ln.awb})):
            for awb, order_id in (
                await self.session.execute(
                    select(Shipment.awb, Shipment.order_id).where(Shipment.awb.in_(part))
                )
            ).all():
                awb_map[awb.strip().lower()] = order_id
        ref_map: dict[str, uuid.UUID] = {}
        refs = {ln.order_ref.lstrip("#") for ln in todo if ln.order_ref}
        for part in chunks(sorted(refs | {f"#{r}" for r in refs})):
            for number, order_id in (
                await self.session.execute(
                    select(Order.order_number, Order.id).where(Order.order_number.in_(part))
                )
            ).all():
                ref_map[number.lstrip("#").strip().lower()] = order_id
        count = 0
        for ln in todo:
            order_id = awb_map.get((ln.awb or "").strip().lower())
            method = "awb"
            if order_id is None and ln.order_ref:
                order_id = ref_map.get(ln.order_ref.lstrip("#").strip().lower())
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
                    select(CodRemittanceLine).where(CodRemittanceLine.matched_order_id.is_(None))
                )
            )
            .scalars()
            .all()
        )
        matched = await self._auto_match(list(lines))
        refs = (
            await self.session.execute(select(CodRemittanceLine.remittance_reference).distinct())
        ).scalars()
        linked = await auto_link_bank_credits(self.session, MODULE, list(refs), user=user)
        await audit(
            self.session,
            user,
            "remittance.cod.rematch",
            "cod_remittance_line",
            "bulk",
            new={"matched": matched, "bank_links": linked},
        )
        await self.session.commit()
        return {"matched": matched, "still_unmatched": len(lines) - matched, "bank_links": linked}

    async def _find_order(self, query: str) -> Order | None:
        """Exact OMS order id (UUID), order number or AWB. Never fuzzy."""
        text = query.strip()
        if not text:
            return None
        try:
            order = await self.session.get(Order, uuid.UUID(text))
            if order is not None:
                return order
        except ValueError:
            pass
        wanted = text.lstrip("#").casefold()
        order = await self.session.scalar(
            select(Order).where(func.lower(func.ltrim(Order.order_number, "#")) == wanted)
        )
        if order is not None:
            return order
        return await self.session.scalar(
            select(Order)
            .join(Shipment, Shipment.order_id == Order.id)
            .where(func.lower(func.trim(Shipment.awb)) == text.casefold())
            .limit(1)
        )

    async def _match_preview(self, line: CodRemittanceLine, order: Order) -> dict[str, Any]:
        """What matching `line` to `order` would do, computed server-side. The
        UI only displays this; the confirm step recomputes it.
        """
        eligible = await self._eligible_order_ids([order.id])
        blockers: list[str] = []
        if order.payment_type != PaymentType.COD:
            blockers.append("Only a COD order can receive a COD remittance.")
        elif order.id not in eligible:
            blockers.append(
                "This order has not been delivered, so a COD remittance cannot be matched to it."
            )
        if line.matched_order_id == order.id:
            blockers.append("This remittance line is already matched to this order.")
        other_lines = (
            (
                await self.session.execute(
                    select(CodRemittanceLine).where(
                        CodRemittanceLine.matched_order_id == order.id,
                        CodRemittanceLine.id != line.id,
                    )
                )
            )
            .scalars()
            .all()
        )
        other = sum((dec(ln.remittance_amount) for ln in other_lines), ZERO)
        adjustment = dec(
            await self.session.scalar(
                select(func.coalesce(func.sum(ReconciliationAdjustment.amount), 0)).where(
                    ReconciliationAdjustment.module == MODULE,
                    ReconciliationAdjustment.order_id == order.id,
                    ReconciliationAdjustment.reversed_at.is_(None),
                )
            )
        )
        expected = dec(order.total_amount)
        line_amount = dec(line.remittance_amount)
        projected = other + line_amount
        difference = projected + adjustment - expected
        status = cod_status(expected, projected, adjustment, ZERO)
        warnings: list[str] = []
        if difference < -TOLERANCE:
            warnings.append(
                f"Partial settlement: after this match the order is still short by "
                f"{money(-difference)}."
            )
        elif difference > TOLERANCE:
            warnings.append(
                f"Excess: after this match the order is over-remitted by {money(difference)}."
            )
        if line.matched_order_id and line.matched_order_id != order.id:
            warnings.append("This line is currently matched to another order and will be moved.")
        shipment = await self.session.scalar(
            select(Shipment)
            .where(Shipment.order_id == order.id)
            .order_by(Shipment.created_at.desc())
            .limit(1)
        )
        customer = order.customer.full_name if order.customer else None
        return {
            "order_id": order.id,
            "order_number": order.order_number,
            "awb": shipment.awb if shipment else None,
            "customer_name": customer,
            "expected_amount": money(expected),
            "line_amount": money(line_amount),
            "other_remitted": money(other),
            "adjustment_amount": money(adjustment),
            "projected_total": money(projected),
            "projected_difference": money(difference),
            "projected_status": status,
            "projected_status_label": STATUS_LABELS[status],
            "blockers": blockers,
            "warnings": warnings,
            "needs_acknowledgement": abs(difference) > TOLERANCE,
        }

    async def lookup_order_for_line(self, line_id: uuid.UUID, query: str) -> dict[str, Any]:
        line = await self.session.get(CodRemittanceLine, line_id)
        if line is None:
            raise NotFoundError("Remittance line not found.")
        order = await self._find_order(query)
        if order is None:
            return {"found": False, "preview": None}
        await self.session.refresh(order, attribute_names=["customer"])
        return {"found": True, "preview": await self._match_preview(line, order)}

    async def manual_match(
        self,
        line_id: uuid.UUID,
        order_id: uuid.UUID,
        user: User,
        *,
        confirm: bool = False,
        acknowledge_difference: bool = False,
    ) -> None:
        """Match one remittance line to an order. Requires explicit
        confirmation; a match that leaves a difference (a partial settlement
        or an excess) also needs it acknowledged. Audit-logged with the
        amounts involved.
        """
        line = await self.session.get(CodRemittanceLine, line_id)
        if line is None:
            raise NotFoundError("Remittance line not found.")
        order = await self.session.get(Order, order_id)
        if order is None:
            raise NotFoundError("Order not found.")
        if not confirm:
            raise ValidationError("Confirm the match before saving.")
        await self.session.refresh(order, attribute_names=["customer"])
        preview = await self._match_preview(line, order)
        if preview["blockers"]:
            raise ValidationError(preview["blockers"][0])
        if preview["needs_acknowledgement"] and not acknowledge_difference:
            raise ValidationError(
                f"This match leaves a difference of {preview['projected_difference']}. "
                "Accept the difference to continue."
            )
        previous = {
            "matched_order_id": str(line.matched_order_id) if line.matched_order_id else None,
            "method": line.match_method,
        }
        line.matched_order_id = order_id
        line.match_method = "manual"
        line.matched_by = user.id
        await audit(
            self.session,
            user,
            "remittance.cod.manual_match",
            "cod_remittance_line",
            line.id,
            previous=previous,
            new={
                "matched_order_id": str(order_id),
                "order_number": order.order_number,
                "line_amount": str(preview["line_amount"]),
                "expected_amount": str(preview["expected_amount"]),
                "projected_difference": str(preview["projected_difference"]),
                "projected_status": preview["projected_status"],
                "difference_acknowledged": bool(acknowledge_difference),
            },
        )
        await self.session.commit()

    async def unmatch(self, line_id: uuid.UUID, user: User) -> None:
        line = await self.session.get(CodRemittanceLine, line_id)
        if line is None:
            raise NotFoundError("Remittance line not found.")
        previous = {
            "matched_order_id": str(line.matched_order_id) if line.matched_order_id else None,
            "method": line.match_method,
        }
        line.matched_order_id = None
        line.match_method = None
        line.matched_by = None
        await audit(
            self.session,
            user,
            "remittance.cod.unmatch",
            "cod_remittance_line",
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
            "remittance.cod.adjustment",
            "reconciliation_adjustment",
            adj.id,
            new={"order_id": str(order_id), "amount": str(adj.amount), "reason": adj.reason},
        )
        await self.session.commit()
        return adj

    # ---------------------------------------------------------------- load

    async def _eligible_order_ids(self, order_ids: list[uuid.UUID]) -> set[uuid.UUID]:
        """Of these orders, the ones that are delivered COD orders at all."""
        out: set[uuid.UUID] = set()
        for part in chunks(order_ids):
            stmt, _ship = self.orders.unified_status_drilldown_query(
                unified_status="delivered", payment_type="cod"
            )
            rows = await self.session.execute(stmt.where(Order.id.in_(list(part))))
            out.update(order.id for order, _s in rows.all())
        return out

    async def _records(self, f: Filters) -> tuple[list[dict], dict[str, Any]]:
        """All delivered-COD records for the filters (date + partner), plus
        shared evidence used by the other views."""
        stmt, ship = self.orders.unified_status_drilldown_query(
            unified_status="delivered",
            payment_type="cod",
            date_from=f.date_from,
            date_to=f.date_to,
        )
        if f.order_id is not None:
            stmt = stmt.where(Order.id == f.order_id)
        stmt = stmt.add_columns(
            effective_courier_name(ship).label("courier_name"),
            effective_delivery_date(ship).label("delivery_date"),
        )
        loaded = (await self.session.execute(stmt)).all()
        order_ids = [o.id for o, *_ in loaded]
        awbs = [s.awb for _o, s, *_ in loaded if s is not None and s.awb]

        lines_by_order: dict[uuid.UUID, list[CodRemittanceLine]] = defaultdict(list)
        adjustments: dict[uuid.UUID, Decimal] = defaultdict(lambda: ZERO)
        for part in chunks(order_ids):
            for ln in (
                (
                    await self.session.execute(
                        select(CodRemittanceLine).where(
                            CodRemittanceLine.matched_order_id.in_(list(part))
                        )
                    )
                )
                .scalars()
                .all()
            ):
                lines_by_order[ln.matched_order_id].append(ln)
            for adj in (
                (
                    await self.session.execute(
                        select(ReconciliationAdjustment).where(
                            ReconciliationAdjustment.module == MODULE,
                            ReconciliationAdjustment.order_id.in_(list(part)),
                            ReconciliationAdjustment.reversed_at.is_(None),
                        )
                    )
                )
                .scalars()
                .all()
            ):
                adjustments[adj.order_id] += dec(adj.amount)
        api: dict[str, CodRemittance] = {}
        for part in chunks(awbs):
            for rem in (
                (
                    await self.session.execute(
                        select(CodRemittance).where(CodRemittance.awb.in_(list(part)))
                    )
                )
                .scalars()
                .all()
            ):
                api[rem.awb] = rem

        ledger = await load_ledger(self.session)
        evidence = evidence_from(ledger, MODULE)
        refs = {ln.remittance_reference for ls in lines_by_order.values() for ln in ls}
        line_bank = allocate_to_lines(await reference_lines(self.session, MODULE, refs), evidence)

        now = datetime.now(UTC)
        records: list[dict] = []
        partner_filter = f.partner.strip().casefold() if f.partner else None
        for order, shipment, courier_name, delivery_date in loaded:
            partner = partner_display_name(courier_name) if courier_name else "Unknown"
            if partner_filter and partner.casefold() != partner_filter:
                continue
            lines = lines_by_order.get(order.id, [])
            expected = dec(order.total_amount)
            adj = adjustments.get(order.id, ZERO)
            payout_indicator = False
            payout_failed = False
            source = None
            remitted = ZERO
            confirmed = ZERO
            manual = ZERO
            covered = False
            references: list[str] = []
            remitted_on: datetime | None = None
            bank_date: datetime | None = None
            if lines:
                source = "upload"
                covered = True
                for ln in lines:
                    amount = dec(ln.remittance_amount)
                    remitted += amount
                    if ln.remittance_reference and ln.remittance_reference not in references:
                        references.append(ln.remittance_reference)
                    if ln.remittance_date and (
                        remitted_on is None or ln.remittance_date > remitted_on
                    ):
                        remitted_on = ln.remittance_date
                    applied = line_bank.get(ln.id)
                    covered = covered and applied is not None and applied.covered
                    if applied is None or applied.confirmed == 0:
                        continue
                    confirmed += applied.confirmed
                    manual += applied.manual
                    credit_date = evidence.credit_dates.get(applied.reference)
                    if credit_date and (bank_date is None or credit_date > bank_date):
                        bank_date = credit_date
            elif shipment is not None and shipment.awb in api:
                rem = api[shipment.awb]
                payload = rem.raw_external_payload or {}
                hover_text = payload.get("cod_recieved_hover_text")
                # `"initiated"` is a status from an EARLIER, now-superseded
                # CRF vocabulary (`pending`/`initiated`/`success`) -- kept
                # here only so historical rows synced before the
                # `/cod/remittance` fix (CONFIRMED status vocabulary:
                # `pending`/`processed`/`failed`) still classify the same
                # way; no current sync will ever write "initiated" again.
                crf_initiated = payload.get("status") == "initiated"
                # A confirmed-real CRF status of "failed" means Shiprocket
                # explicitly reported the payout attempt FAILED -- this
                # must never be read as payout evidence (it is not "in
                # progress" or "reported", it is a stop) and must win over
                # every other indicator for this AWB.
                crf_failed = payload.get("status") == "failed"
                if crf_failed:
                    payout_failed = True
                elif rem.remitted_amount is not None and dec(rem.remitted_amount) > 0:
                    source = "shiprocket_api"
                    remitted = dec(rem.remitted_amount)
                    remitted_on = rem.settlement_date
                    if rem.remittance_reference:
                        references.append(rem.remittance_reference)
                    # A recorded manual verification, capped so a statement
                    # credit is never used twice (see `BankLedger`).
                    confirmed = min(ledger.claims.get(rem.awb, ZERO), remitted)
                    if confirmed > 0:
                        manual = confirmed
                        covered = confirmed >= remitted - TOLERANCE
                        bank_date = rem.bank_credited_on
                elif rem.remittance_reference or rem.settlement_date or hover_text or crf_initiated:
                    # Order-detail payout indicator (UTR / date / hover text /
                    # a CRF "initiated" status), no amount: the amount, and so
                    # the reconciliation status, stays unknown. All of these
                    # are treated identically -- presence counts, wording
                    # never does.
                    source = "shiprocket_order"
                    payout_indicator = True
                    remitted_on = rem.settlement_date
                    if rem.remittance_reference:
                        references.append(rem.remittance_reference)
            status = cod_status(expected, remitted, adj, confirmed, covered)
            before = remitted - expected
            order_dt = delivery_date or order.order_datetime
            age_days = max((now - order_dt).days, 0)
            records.append(
                {
                    "order_id": order.id,
                    "order_number": order.order_number,
                    "awb": shipment.awb if shipment else None,
                    "customer_name": order.customer.full_name if order.customer else None,
                    "partner": partner,
                    "service_name": courier_name,
                    "order_datetime": order.order_datetime,
                    "order_amount": money(dec(order.total_amount)),
                    "payment_status": _enum_value(order.payment_status),
                    "delivery_status": _enum_value(shipment.current_status) if shipment else None,
                    "delivery_date": delivery_date,
                    "expected_amount": money(expected),
                    "remittance_amount": money(remitted),
                    "amount_received": money(confirmed),
                    "adjustment_amount": money(adj),
                    "difference": money(remitted + adj - expected),
                    "difference_before_adjustments": money(before),
                    "adjustment_effect": adjustment_effect(
                        before=before,
                        adjustment=adj,
                        status_before=cod_status(expected, remitted, ZERO, confirmed, covered),
                        status_after=status,
                    ),
                    **bank_position(expected, confirmed, manual, covered),
                    "remittance_reference": ", ".join(references) or None,
                    "remittance_date": remitted_on,
                    "bank_credit_date": bank_date,
                    "status": status,
                    "status_label": STATUS_LABELS[status],
                    "source": source,
                    "payout_reported": status == "pending" and payout_indicator,
                    "payout_failed": payout_failed,
                    "age_days": age_days,
                    "line_count": len(lines),
                    "_settlement_days": sorted(
                        {to_ist_date(ln.remittance_date) for ln in lines if ln.remittance_date}
                    ),
                }
            )
        shared = {
            "evidence": evidence,
            "lines_by_order": lines_by_order,
            "order_ids": order_ids,
            "line_bank": line_bank,
            "ledger": ledger,
            "api": api,
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
            # Same population as the ageing report: orders still owed or awaiting bank.
            out = [
                r
                for r in out
                if r["status"] in ("pending", "short", "partially_reconciled")
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
                or term in (r["awb"] or "").casefold()
                or term in (r["customer_name"] or "").casefold()
                or term in (r["remittance_reference"] or "").casefold()
            ]
        return sorted(out, key=lambda r: (r["order_datetime"], str(r["order_id"])), reverse=True)

    # ------------------------------------------------------- unmatched pool

    async def _unmatched(self, f: Filters, evidence: BankEvidence) -> list[dict]:
        """Remittance lines with no (eligible) order, and bank credits not
        allocated to any remittance / settlement. Always visible."""
        stmt = select(CodRemittanceLine)
        if f.date_from or f.date_to:
            conds = []
            if f.date_from:
                conds.append(CodRemittanceLine.remittance_date >= f.date_from)
            if f.date_to:
                conds.append(CodRemittanceLine.remittance_date <= f.date_to)
            from sqlalchemy import and_, or_

            stmt = stmt.where(or_(CodRemittanceLine.remittance_date.is_(None), and_(*conds)))
        lines = (await self.session.execute(stmt)).scalars().all()
        matched_ids = [ln.matched_order_id for ln in lines if ln.matched_order_id]
        eligible = await self._eligible_order_ids(list(set(matched_ids))) if matched_ids else set()
        partner_filter = f.partner.strip().casefold() if f.partner else None
        rows: list[dict] = []
        for ln in lines:
            if ln.matched_order_id is not None and ln.matched_order_id in eligible:
                continue
            partner = partner_display_name(ln.partner_name)
            if partner_filter and partner.casefold() != partner_filter:
                continue
            reason = (
                "No order found for this AWB / order reference"
                if ln.matched_order_id is None
                else "Matched order is not a delivered COD order"
            )
            rows.append(
                {
                    "kind": "remittance_line",
                    "id": ln.id,
                    "partner": partner,
                    "awb": ln.awb,
                    "order_ref": ln.order_ref,
                    "reference": ln.remittance_reference,
                    "amount": money(dec(ln.remittance_amount)),
                    "date": ln.remittance_date,
                    "reason": reason,
                    "matched_order_id": ln.matched_order_id,
                }
            )
        # Unlinked bank credits belong to no partner, so a partner filter must never
        # hide them: an unexplained credit is a financial exception.
        rows.extend(await self._unlinked_credits(f))
        return rows

    async def _unlinked_credits(self, f: Filters) -> list[dict]:
        ledger = await load_ledger(self.session)
        out = []
        for credit in ledger.credits:
            if credit.credit_date and (
                (f.date_from and credit.credit_date < f.date_from)
                or (f.date_to and credit.credit_date > f.date_to)
            ):
                continue
            remaining = ledger.free(credit)
            if remaining <= TOLERANCE:
                continue
            out.append(
                {
                    "kind": "bank_credit",
                    "id": credit.id,
                    "partner": None,
                    "awb": None,
                    "order_ref": None,
                    "reference": credit.reference,
                    "amount": money(remaining),
                    "date": credit.credit_date,
                    "reason": "Bank credit not linked to any remittance or settlement",
                    "matched_order_id": None,
                }
            )
        return out

    # ---------------------------------------------------------------- views

    async def _total_cod_orders(self, f: Filters) -> tuple[int, Decimal]:
        """Every non-cancelled COD order in range -- not just the delivered
        ones this module otherwise reconciles. A distinct, wider population
        from `delivered_orders`, so it is computed separately rather than
        derived from `_records()`."""
        stmt = select(func.count(Order.id), func.coalesce(func.sum(Order.total_amount), 0)).where(
            Order.payment_type == PaymentType.COD,
            Order.status != OrderStatus.CANCELLED,
        )
        if f.date_from:
            stmt = stmt.where(Order.order_datetime >= f.date_from)
        if f.date_to:
            stmt = stmt.where(Order.order_datetime <= f.date_to)
        count, amount = (await self.session.execute(stmt)).one()
        return count, dec(amount)

    async def _crf_records(self) -> list[dict[str, Any]]:
        """Every individual CRF transaction straight from Shiprocket's
        `POST /cod/remittance` feed (CONFIRMED DIRECTLY BY SHIPROCKET
        SUPPORT -- see `ShiprocketAdapter.fetch_cod_remittances`). The
        confirmed response carries only `awb`, `amount`, `status`; `utr`,
        `order_id` (and `crf_id`, below) stay `None` for any row sourced
        from this endpoint -- never invented. A `"processed"` row comes
        from `CodRemittanceLine` (`CodReconciliationService.
        sync_remittances` only ever creates one there with that partner
        name, from a CRF record). A `"pending"`/`"failed"` row comes from
        `CodRemittance`'s `raw_external_payload`, where the ORIGINAL
        `amount` Shiprocket reported is preserved even though it is
        deliberately never written to `remitted_amount` (see
        `sync_remittances`): this view can show it precisely because it
        is read-only and does not feed `cod_status()`/Payment
        Success-Initiated-Failed-Pending in any way. (`"initiated"` rows
        can still appear here too, purely for backward compatibility with
        rows synced before this fix, under the now-superseded
        `pending`/`initiated`/`success` vocabulary -- no current sync
        ever writes that status again.)
        """
        rows: list[dict[str, Any]] = []
        lines = (
            (
                await self.session.execute(
                    select(CodRemittanceLine).where(
                        CodRemittanceLine.partner_name == "Shiprocket (CRF)"
                    )
                )
            )
            .scalars()
            .all()
        )
        for ln in lines:
            raw = ln.raw_row or {}
            # Only a REAL crf_id Shiprocket actually returned -- never the
            # internal (awb, amount, status) fingerprint used as a
            # fallback dedupe key when no real identifier is present (see
            # `_cod_entry_dedupe_key`); that fingerprint is not a CRF id
            # and must not be displayed as one.
            real_crf_id = raw.get("crf_id")
            if real_crf_id is None and ln.dedupe_key.startswith("shiprocket_crf:"):
                real_crf_id = ln.dedupe_key.removeprefix("shiprocket_crf:")
            rows.append(
                {
                    "crf_id": real_crf_id,
                    "utr": ln.remittance_reference,
                    "awb": ln.awb,
                    "order_id": ln.order_ref,
                    "amount": dec(ln.remittance_amount),
                    "date": ln.remittance_date,
                    "status": "processed",
                    "matched_order_id": ln.matched_order_id,
                }
            )
        api_rows = (
            (
                await self.session.execute(
                    select(CodRemittance).where(CodRemittance.raw_external_payload.is_not(None))
                )
            )
            .scalars()
            .all()
        )
        for rem in api_rows:
            payload = rem.raw_external_payload or {}
            status = payload.get("status")
            if status not in ("pending", "failed", "initiated"):
                continue
            rows.append(
                {
                    "crf_id": payload.get("crf_id"),
                    "utr": payload.get("utr") or rem.remittance_reference,
                    "awb": rem.awb,
                    "order_id": payload.get("order_id") or rem.shiprocket_order_id,
                    "amount": dec(payload.get("amount")),
                    "date": rem.settlement_date,
                    "status": status,
                    "matched_order_id": None,
                }
            )
        rows.sort(key=lambda r: (r["date"] or datetime.min.replace(tzinfo=UTC)), reverse=True)
        return rows

    async def _crf_amounts(self) -> dict[str, dict[str, Any]]:
        """Real Rupee totals straight from the CRF feed (see `_crf_records`),
        for display/transparency only. These are NOT fed into `cod_status()`
        or the Payment Success/Initiated/Failed/Pending KPIs above, which
        stay order-level and (for Success) bank-evidence-gated -- unchanged
        by this method.

        Response keys are kept as `crf_success_amount`/`crf_initiated_
        amount`/`crf_pending_amount` for backward API/frontend
        compatibility with the dashboard cards built under the earlier,
        now-superseded `pending`/`initiated`/`success` vocabulary --
        `crf_success_amount` is populated from CONFIRMED `"processed"`
        rows (the new name for that same bucket); `crf_initiated_amount`
        will be 0 for any environment where every row was synced under
        the current, confirmed vocabulary (since no current sync ever
        writes `"initiated"` again), but still correctly reports any
        pre-fix historical rows. `crf_failed_amount` is a NEW, additive
        key -- a confirmed `"failed"` CRF must be visible here, never
        silently folded into `crf_pending_amount`; the dashboard UI does
        not yet render this key (out of this change's scope), so adding a
        "Payment Failed" card is tracked as separate, required follow-up
        work, not implemented here.
        """
        totals: dict[str, Decimal] = defaultdict(lambda: ZERO)
        counts: dict[str, int] = defaultdict(int)
        for row in await self._crf_records():
            totals[row["status"]] += row["amount"]
            counts[row["status"]] += 1
        return {
            "crf_success_amount": {
                "count": counts["processed"],
                "amount": money(totals["processed"]),
            },
            "crf_initiated_amount": {
                "count": counts["initiated"],
                "amount": money(totals["initiated"]),
            },
            "crf_failed_amount": {"count": counts["failed"], "amount": money(totals["failed"])},
            "crf_pending_amount": {"count": counts["pending"], "amount": money(totals["pending"])},
        }

    async def crf_transactions(
        self, *, status: str | None, page_params: PageParams
    ) -> tuple[list[dict[str, Any]], int]:
        """Paginated transaction-detail view behind the CRF amount KPIs."""
        rows = await self._crf_records()
        if status:
            rows = [r for r in rows if r["status"] == status]
        total = len(rows)
        start = page_params.offset
        return rows[start : start + page_params.page_size], total

    async def summary(self, f: Filters) -> dict[str, Any]:
        records, shared = await self._records(f)
        if f.status:  # the status filter narrows every card, percentage and chart too
            records = [r for r in records if r["status"] == f.status]
        unmatched = await self._unmatched(f, shared["evidence"])
        n = len(records)
        total_expected = money(sum((r["expected_amount"] for r in records), ZERO))
        by_status = {s: [r for r in records if r["status"] == s] for s in COD_STATUSES}
        received = [r for r in records if r["remittance_amount"] != 0]
        bank_confirmed = [r for r in records if r["amount_received"] != 0]
        short = by_status["short"]
        excess = by_status["excess"]
        pending = by_status["pending"]
        pending_amount = money(
            sum((r["expected_amount"] + r["adjustment_amount"] for r in pending), ZERO)
        )
        short_amount = money(sum((-r["difference"] for r in short), ZERO))
        excess_amount = money(sum((r["difference"] for r in excess), ZERO))
        matched = by_status["matched"]
        initiated = [r for r in records if _is_payment_initiated(r)]
        initiated_amount = money(sum((r["expected_amount"] for r in initiated), ZERO))
        payment_failed = [r for r in records if _is_payment_failed(r)]
        payment_failed_amount = money(sum((r["expected_amount"] for r in payment_failed), ZERO))
        payment_pending = [r for r in records if _is_payment_pending(r)]
        payment_pending_amount = money(sum((r["expected_amount"] for r in payment_pending), ZERO))
        payment_success = [r for r in records if _is_payment_success(r)]
        payment_success_amount = money(sum((r["expected_amount"] for r in payment_success), ZERO))
        matched_amount = money(sum((r["expected_amount"] for r in matched), ZERO))
        total_cod_orders, total_cod_amount = await self._total_cod_orders(f)
        unmatched_lines = [u for u in unmatched if u["kind"] == "remittance_line"]
        unmatched_credits = [u for u in unmatched if u["kind"] == "bank_credit"]
        unmatched_line_amount = money(sum((u["amount"] for u in unmatched_lines), ZERO))
        unmatched_credit_amount = money(sum((u["amount"] for u in unmatched_credits), ZERO))
        remitted_total = money(sum((r["remittance_amount"] for r in records), ZERO))
        lines_in_dataset = sum(r["line_count"] for r in records)
        tx_total = lines_in_dataset + len(unmatched_lines)
        kpis = {
            "delivered_orders": _kpi(records),
            "expected_collection": _kpi(records),
            "remittance_received": {"count": len(received), "amount": remitted_total},
            "bank_confirmed": {
                "count": len(bank_confirmed),
                "amount": money(sum((r["amount_received"] for r in records), ZERO)),
            },
            "pending": {"count": len(pending), "amount": pending_amount},
            "short": {"count": len(short), "amount": short_amount},
            "excess": {"count": len(excess), "amount": excess_amount},
            "unmatched": {"count": len(unmatched_lines), "amount": unmatched_line_amount},
            "unmatched_bank_credits": {
                "count": len(unmatched_credits),
                "amount": unmatched_credit_amount,
            },
            "reconciled": {
                "count": len(matched),
                "amount": matched_amount,
            },
            "partially_reconciled": _kpi(by_status["partially_reconciled"]),
            # The 5-KPI payment-status summary (see PAYMENT_INITIATED_STATUSES).
            # "total_cod_orders" is a wider population (every COD order, not
            # just delivered ones) so it has no drilldown in this module.
            "total_cod_orders": {"count": total_cod_orders, "amount": money(total_cod_amount)},
            "payment_success": {"count": len(payment_success), "amount": payment_success_amount},
            "payment_initiated": {"count": len(initiated), "amount": initiated_amount},
            "payment_failed": {"count": len(payment_failed), "amount": payment_failed_amount},
            "payment_pending": {"count": len(payment_pending), "amount": payment_pending_amount},
            **await self._crf_amounts(),
        }
        pct = [
            percentage_item(
                "completion",
                "Reconciliation completion",
                len(matched),
                n,
                kind="count",
                numerator_label="fully reconciled orders",
                denominator_label="delivered COD orders",
                drill="reconciled",
            ),
            percentage_item(
                "completion_value",
                "Reconciliation completion (value)",
                kpis["reconciled"]["amount"],
                total_expected,
                kind="value",
                numerator_label="expected COD of fully reconciled orders",
                denominator_label="expected COD collection",
                drill="reconciled",
            ),
            percentage_item(
                "remitted",
                "COD settled (courier-reported)",
                len(received),
                n,
                kind="count",
                numerator_label="orders with a courier-reported remittance",
                denominator_label="delivered COD orders",
                drill="received",
            ),
            percentage_item(
                "pending",
                "Pending remittance",
                len(pending),
                n,
                kind="count",
                numerator_label="orders with no courier-reported remittance",
                denominator_label="delivered COD orders",
                drill="pending",
            ),
            percentage_item(
                "pending_value",
                "Pending remittance (value)",
                pending_amount,
                total_expected,
                kind="value",
                numerator_label="COD amount with no courier-reported remittance",
                denominator_label="expected COD collection",
                drill="pending",
            ),
            percentage_item(
                "short",
                "Short remittance",
                len(short),
                n,
                kind="count",
                numerator_label="orders remitted short",
                denominator_label="delivered COD orders",
                drill="short",
            ),
            percentage_item(
                "short_value",
                "Short remittance (value)",
                short_amount,
                total_expected,
                kind="value",
                numerator_label="short amount",
                denominator_label="expected COD collection",
                drill="short",
            ),
            percentage_item(
                "excess",
                "Excess remittance",
                len(excess),
                n,
                kind="count",
                numerator_label="orders remitted in excess",
                denominator_label="delivered COD orders",
                drill="excess",
            ),
            percentage_item(
                "excess_value",
                "Excess remittance (value)",
                excess_amount,
                total_expected,
                kind="value",
                numerator_label="excess amount",
                denominator_label="expected COD collection",
                drill="excess",
            ),
            percentage_item(
                "matched_tx",
                "Matched transactions",
                lines_in_dataset,
                tx_total,
                kind="count",
                numerator_label="remittance lines matched to delivered COD orders",
                denominator_label="remittance lines in this view",
                drill="received",
            ),
            percentage_item(
                "unmatched_tx",
                "Unmatched transactions",
                len(unmatched_lines),
                tx_total,
                kind="count",
                numerator_label="unmatched remittance lines",
                denominator_label="remittance lines in this view",
                drill="unmatched",
            ),
        ]
        partners = await self._partner_rows(records, unmatched_lines)
        extra = bank_and_adjustment_kpis(records, "amount_received")
        kpis["bank_confirmed"]["statement_matched"] = extra["bank_position"]["statement_matched"]
        kpis["bank_confirmed"]["manually_verified"] = extra["bank_position"]["manually_verified"]
        kpis.update(extra)
        return {
            "kpis": kpis,
            "percentages": pct,
            "status_breakdown": [
                {
                    "status": s,
                    "label": STATUS_LABELS[s],
                    "count": len(by_status[s]),
                    "amount": money(sum((r["expected_amount"] for r in by_status[s]), ZERO)),
                    "percent_of_orders": (len(by_status[s]) / n * 100) if n else None,
                }
                for s in COD_STATUSES
            ],
            "partners": partners,
            "denominators": {
                "delivered_orders": n,
                "expected_amount": total_expected,
                "remittance_lines": tx_total,
                "total_cod_orders": total_cod_orders,
                "total_cod_amount": money(total_cod_amount),
            },
            "data_coverage": await self._data_coverage(payment_pending),
        }

    async def _charges_table_exists(self) -> bool:
        """Probes `shiprocket_shipment_charges` (migration f2b8d4a6c0e1, which a database
        may not have yet) inside a SAVEPOINT, so a missing table rolls back only the
        probe and never aborts the summary's transaction."""
        try:
            async with self.session.begin_nested():
                await self.session.execute(select(ShiprocketShipmentCharge.id).limit(1))
        except (ProgrammingError, OperationalError):
            return False
        return True

    async def _unchecked_pending(self, pending: list[dict]) -> int | None:
        """Payment-Pending orders whose Shiprocket order record was never read
        (`GET /orders/show` leaves a `ShiprocketShipmentCharge` row per AWB, even
        when its payout fields are blank). Such an order is "unknown", not "no
        payout"; it can only be Pending because nothing says otherwise."""
        if not await self._charges_table_exists():
            return None  # unknown, never reported as 0: the dashboard says so
        awbs = sorted({r["awb"] for r in pending if r["awb"]})
        checked: set[str] = set()
        for part in chunks(awbs):
            checked.update(
                (
                    await self.session.execute(
                        select(ShiprocketShipmentCharge.awb).where(
                            ShiprocketShipmentCharge.awb.in_(list(part))
                        )
                    )
                )
                .scalars()
                .all()
            )
        return sum(1 for r in pending if not r["awb"] or r["awb"] not in checked)

    async def _data_coverage(self, payment_pending: list[dict]) -> dict[str, Any]:
        """What remittance / bank evidence OMS holds at all (all time, not the
        selected date range). Lets the dashboard tell "no remittance reported"
        apart from "no remittance data has been loaded": with no evidence
        loaded, every delivered order is `pending` simply because nothing can
        say otherwise, which is a data limitation, not confirmed non-payment."""

        async def count(stmt) -> int:  # noqa: ANN001
            return int((await self.session.execute(stmt)).scalar_one() or 0)

        lines = await count(select(func.count(CodRemittanceLine.id)))
        api_reported = await count(
            select(func.count(CodRemittance.id)).where(CodRemittance.remitted_amount > 0)
        )
        # Shiprocket order-detail payout indicators (UTR / date / hover text)
        # with no amount. The hover-text key lives inside the JSON
        # `raw_external_payload` column (no dedicated column, no migration),
        # so it is checked in Python rather than with a dialect-specific JSON
        # operator -- this table is the small, order-detail-synced one, not
        # the full orders table.
        no_amount_rows = (
            await self.session.execute(
                select(
                    CodRemittance.remittance_reference,
                    CodRemittance.settlement_date,
                    CodRemittance.raw_external_payload,
                ).where(
                    CodRemittance.remitted_amount.is_(None) | (CodRemittance.remitted_amount <= 0)
                )
            )
        ).all()
        order_payouts = sum(
            1
            for reference, settlement_date, raw in no_amount_rows
            if reference
            or settlement_date
            or (
                isinstance(raw, dict)
                and (raw.get("cod_recieved_hover_text") or raw.get("status"))
            )
        )
        bank_credits = await count(select(func.count(BankCredit.id)))
        manual_verifications = await count(
            select(func.count(CodRemittance.id)).where(CodRemittance.bank_verified_at.is_not(None))
        )
        last_upload = (
            await self.session.execute(
                select(func.max(SettlementFile.created_at)).where(SettlementFile.module == MODULE)
            )
        ).scalar_one()
        last_api_sync = (
            await self.session.execute(select(func.max(CodRemittance.synced_at)))
        ).scalar_one()
        has_remittance_data = lines > 0 or api_reported > 0 or order_payouts > 0
        return {
            "remittance_lines": lines,
            "api_reported_remittances": api_reported,
            "order_payout_references": order_payouts,
            "bank_credits": bank_credits,
            "manual_bank_verifications": manual_verifications,
            "last_upload_at": last_upload,
            "last_api_sync_at": last_api_sync,
            "api_sync_configured": bool(settings.SHIPROCKET_EMAIL and settings.SHIPROCKET_PASSWORD),
            "has_remittance_data": has_remittance_data,
            "has_bank_data": bank_credits > 0 or manual_verifications > 0,
            # True: "Payment Pending" means "nothing loaded to say otherwise".
            "pending_is_data_limited": not has_remittance_data,
            # In the selected period: Payment-Pending orders and how many of them were
            # never checked against Shiprocket's order API (nor are in any report).
            "pending_orders": len(payment_pending),
            "pending_unchecked_orders": (
                unchecked := await self._unchecked_pending(payment_pending)
            ),
            # False: the "was it read from Shiprocket" evidence cannot be queried yet.
            "pending_check_available": unchecked is not None,
        }

    async def drilldown_records(self, f: Filters, kpi: str | None) -> tuple[list[dict], dict]:
        """Every record behind a card (all pages), each with the amount it
        contributes, plus totals with and without the search term."""
        records, _shared = await self._records(f)
        label, value = MEASURES.get(kpi or "", EXPECTED_MEASURE)
        found = self._apply(records, f, kpi)
        base = self._apply(records, replace(f, q=None), kpi) if f.q else found
        rows = [{**r, "contribution": money(value(r))} for r in found]
        totals = drill_totals(label, rows, [money(value(r)) for r in base])
        return rows, totals

    async def records(
        self, f: Filters, *, kpi: str | None, page_params: PageParams
    ) -> tuple[list[dict], int, dict]:
        rows, totals = await self.drilldown_records(f, kpi)
        page, total = _paginate(rows, page_params)
        return page, total, totals

    async def drilldown_unmatched(self, f: Filters, kind: str | None) -> tuple[list[dict], dict]:
        evidence = await load_bank_evidence(self.session, MODULE)
        everything = await self._unmatched(f, evidence)
        if kind in ("remittance_line", "bank_credit"):
            everything = [r for r in everything if r["kind"] == kind]
        rows = everything
        if f.q and f.q.strip():
            term = f.q.strip().casefold()
            rows = [
                r
                for r in rows
                if term in (r["awb"] or "").casefold()
                or term in (r["order_ref"] or "").casefold()
                or term in (r["reference"] or "").casefold()
            ]
        rows = sorted(
            rows,
            key=lambda r: (r["date"] is None, r["date"] or datetime.min.replace(tzinfo=UTC)),
            reverse=True,
        )
        rows = [{**r, "contribution": r["amount"]} for r in rows]
        totals = drill_totals(
            {
                "remittance_line": "Unmatched courier-reported amount",
                "bank_credit": "Unlinked bank credit amount",
            }.get(kind or "", "Mixed list: see the separate totals below"),
            rows,
            [r["amount"] for r in everything],
            kinds={
                key: label
                for key, label in (
                    ("remittance_line", "Remittance lines"),
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
        page, total = _paginate(rows, page_params)
        return page, total, totals

    async def order_detail(self, order_id: uuid.UUID) -> dict[str, Any]:
        """One order's reconciliation record (same computation as the
        dashboard) and the evidence behind it."""
        records, shared = await self._records(Filters(order_id=order_id))
        if not records:
            raise NotFoundError("This order is not part of COD reconciliation.")
        record = records[0]
        lines = [
            {
                "id": ln.id,
                "source": "upload",
                "partner": partner_display_name(ln.partner_name),
                "awb": ln.awb,
                "order_ref": ln.order_ref,
                "reference": ln.remittance_reference,
                "amount": money(dec(ln.remittance_amount)),
                "date": ln.remittance_date,
                "match_method": ln.match_method,
            }
            for ln in shared["lines_by_order"].get(order_id, [])
        ]
        references = [ln["reference"] for ln in lines]
        bank_links = await bank_links_for(self.session, MODULE, references)
        rem = shared["api"].get(record["awb"]) if record["source"] == "shiprocket_api" else None
        if rem is not None and rem.bank_verified_at is not None:
            bank_links.append(
                {
                    "id": rem.id,
                    "module": MODULE,
                    "reference": rem.remittance_reference or rem.awb,
                    "linked_amount": record["amount_received"],
                    "method": "manual_verification",
                    "verification": "manually_verified",
                    "verification_label": "Manually verified",
                    "bank_reference": rem.bank_credit_reference,
                    # Verifications recorded before reasons became mandatory have none.
                    "reason": rem.bank_verification_reason,
                    "credit_amount": money(dec(rem.bank_credit_amount)),
                    "credit_date": rem.bank_credited_on,
                    "narration": None,
                }
            )
        return {
            "module": MODULE,
            "record": {k: v for k, v in record.items() if not k.startswith("_")},
            "lines": lines,
            "bank_links": bank_links,
            "bank_references": await reference_positions(self.session, MODULE, references),
            "adjustments": await adjustments_for(self.session, MODULE, order_id),
        }

    async def export_drilldown_xlsx(
        self, f: Filters, *, kpi: str | None, unmatched: bool, kind: str | None
    ) -> bytes:
        if unmatched:
            rows, totals = await self.drilldown_unmatched(f, kind)
            columns = [
                ("Type", lambda r: r["kind"].replace("_", " ")),
                ("Courier partner", lambda r: r["partner"]),
                ("AWB", lambda r: r["awb"]),
                ("Order reference", lambda r: r["order_ref"]),
                ("Remittance reference / UTR", lambda r: r["reference"]),
                ("Settlement / credit date", lambda r: fmt_dt(r["date"])),
                ("Amount", lambda r: float(r["amount"])),
                ("Why unmatched", lambda r: r["reason"]),
            ]
        else:
            rows, totals = await self.drilldown_records(f, kpi)
            columns = [
                ("Order number", lambda r: r["order_number"]),
                ("Customer", lambda r: r["customer_name"]),
                ("Order date", lambda r: fmt_dt(r["order_datetime"])),
                ("Payment status", lambda r: r["payment_status"]),
                ("Delivery status", lambda r: r["delivery_status"]),
                ("Delivery date", lambda r: fmt_dt(r["delivery_date"])),
                ("AWB", lambda r: r["awb"]),
                ("Courier partner", lambda r: r["partner"]),
                ("Order amount", lambda r: float(r["order_amount"])),
                ("COD amount", lambda r: float(r["expected_amount"])),
                ("COD settled (courier-reported)", lambda r: float(r["remittance_amount"])),
                ("COD bank confirmed", lambda r: float(r["amount_received"])),
                (
                    "Difference (COD settled + adjustments - COD amount)",
                    lambda r: float(r["difference"]),
                ),
                ("Remittance reference", lambda r: r["remittance_reference"]),
                ("Settlement date", lambda r: fmt_dt(r["remittance_date"])),
                ("Bank credit date", lambda r: fmt_dt(r["bank_credit_date"])),
                ("Reconciliation status", lambda r: r["status_label"]),
                *(
                    (header, lambda r, key=key: export_value(r[key]))
                    for header, key in BANK_EXPORT_COLUMNS
                ),
                (f"Counts toward card: {totals['measure']}", lambda r: float(r["contribution"])),
            ]
        return drilldown_workbook(columns, rows, totals, module=MODULE)

    async def _partner_rows(self, records: list[dict], unmatched_lines: list[dict]) -> list[dict]:
        groups: dict[str, list[dict]] = defaultdict(list)
        for r in records:
            groups[r["partner"]].append(r)
        stray: dict[str, int] = defaultdict(int)
        for u in unmatched_lines:
            stray[u["partner"]] += 1
        out = []
        for partner in sorted(set(groups) | set(stray)):
            rs = groups.get(partner, [])
            n = len(rs)
            matched = [r for r in rs if r["status"] == "matched"]
            out.append(
                {
                    "partner": partner,
                    "delivered_orders": n,
                    "expected_amount": money(sum((r["expected_amount"] for r in rs), ZERO)),
                    "remittance_amount": money(sum((r["remittance_amount"] for r in rs), ZERO)),
                    "amount_received": money(sum((r["amount_received"] for r in rs), ZERO)),
                    "pending_orders": sum(1 for r in rs if r["status"] == "pending"),
                    "short_orders": sum(1 for r in rs if r["status"] == "short"),
                    "excess_orders": sum(1 for r in rs if r["status"] == "excess"),
                    "matched_orders": len(matched),
                    "unmatched_lines": stray.get(partner, 0),
                    "completion_percent": (len(matched) / n * 100) if n else None,
                }
            )
        out.sort(key=lambda p: -p["delivered_orders"])
        return out

    async def report_partners(self, f: Filters) -> list[dict]:
        records, shared = await self._records(f)
        unmatched = await self._unmatched(f, shared["evidence"])
        return await self._partner_rows(
            self._apply(records, f, None), [u for u in unmatched if u["kind"] == "remittance_line"]
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
                    "amount": ZERO,
                    "bank_confirmed_amount": ZERO,
                    "unmatched_lines": 0,
                },
            )

        for order_id in {r["order_id"] for r in records}:
            for ln in shared["lines_by_order"].get(order_id, []):
                day = to_ist_date(ln.remittance_date) if ln.remittance_date else "No date"
                b = bucket(day)
                b["lines"] += 1
                b["amount"] += dec(ln.remittance_amount)
                if ln.id in shared["line_bank"]:
                    b["bank_confirmed_amount"] += shared["line_bank"][ln.id].confirmed
        for u in await self._unmatched(f, evidence):
            if u["kind"] != "remittance_line":
                continue
            b = bucket(to_ist_date(u["date"]) if u["date"] else "No date")
            b["lines"] += 1
            b["unmatched_lines"] += 1
            b["amount"] += u["amount"]
        out = [
            {
                **b,
                "amount": money(b["amount"]),
                "bank_confirmed_amount": money(b["bank_confirmed_amount"]),
            }
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
                "short_orders": 0,
                "short_amount": ZERO,
                "awaiting_bank_orders": 0,
                "awaiting_bank_amount": ZERO,
            }
            for label, _lo, _hi in AGEING_BUCKETS
        }
        for r in records:
            row = rows[ageing_bucket(r["age_days"])]
            if r["status"] == "pending":
                row["pending_orders"] += 1
                row["pending_amount"] += r["expected_amount"] + r["adjustment_amount"]
            elif r["status"] == "short":
                row["short_orders"] += 1
                row["short_amount"] += -r["difference"]
            elif r["status"] == "partially_reconciled":
                row["awaiting_bank_orders"] += 1
                row["awaiting_bank_amount"] += r["remittance_amount"] - r["amount_received"]
        return [
            {**row, **{k: money(v) for k, v in row.items() if isinstance(v, Decimal)}}
            for row in rows.values()
        ]

    async def report_bank_credits(
        self, f: Filters, page_params: PageParams, module: str = MODULE
    ) -> tuple[list[dict], int]:
        ledger = await load_ledger(self.session)
        links: dict[uuid.UUID, list[tuple[BankCreditLink, Decimal]]] = defaultdict(list)
        for link, applied in ledger.links:
            links[link.bank_credit_id].append((link, applied))
        rows = []
        for c in ledger.credits:
            if c.credit_date and (
                (f.date_from and c.credit_date < f.date_from)
                or (f.date_to and c.credit_date > f.date_to)
            ):
                continue
            mine = links.get(c.id, [])
            statement = sum((amount for link, amount in mine if link.method == "auto"), ZERO)
            claimed = ledger.claimed.get(c.id, ZERO)
            manual = sum((a for link, a in mine if link.method != "auto"), ZERO) + claimed
            amount = dec(c.amount)
            linked = statement + manual
            free = ledger.free(c)
            state = (
                "linked"
                if linked > 0 and free <= TOLERANCE
                else ("partially_linked" if linked > 0 else "unlinked")
            )
            details = [
                {**link_row(link, c), "linked_amount": money(applied)} for link, applied in mine
            ]
            references = [
                link.reference if link.module == module else f"{link.reference} ({link.module})"
                for link, _applied in mine
            ]
            if claimed > 0:
                references.append("Shiprocket manual verification (cod)")
            rows.append(
                {
                    "id": c.id,
                    "credit_date": c.credit_date,
                    "amount": money(amount),
                    "reference": c.reference,
                    "narration": c.narration,
                    "linked_amount": money(linked),
                    "statement_matched_amount": money(statement),
                    "manually_verified_amount": money(manual),
                    "unlinked_amount": money(free),
                    "linked_references": ", ".join(references) or None,
                    "link_state": state,
                    "links": details,
                }
            )
        if f.q and f.q.strip():
            term = f.q.strip().casefold()
            rows = [
                r
                for r in rows
                if term in (r["reference"] or "").casefold()
                or term in (r["narration"] or "").casefold()
            ]
        rows.sort(
            key=lambda r: (
                r["credit_date"] is None,
                r["credit_date"] or datetime.min.replace(tzinfo=UTC),
            ),
            reverse=True,
        )
        return _paginate(rows, page_params)

    # --------------------------------------------------------------- manual

    # --------------------------------------------------------------- export

    async def export_xlsx(self, f: Filters, report: str | None = None) -> bytes:
        records, shared = await self._records(f)
        records = self._apply(records, f, None)
        wb = Workbook()
        ws = wb.active
        ws.title = "Order-wise"
        ws.append(
            [
                "Order ID",
                "AWB",
                "Customer",
                "Shipping partner",
                "Order date",
                "Delivery date",
                "COD amount",
                "COD settled (courier-reported)",
                "COD bank confirmed",
                "Difference (COD settled + adjustments - COD amount)",
                "Remittance reference",
                "Remittance date",
                "Bank credit date",
                "Status",
                *(header for header, _key in BANK_EXPORT_COLUMNS),
            ]
        )
        for r in records:
            ws.append(
                [
                    r["order_number"],
                    r["awb"],
                    r["customer_name"],
                    r["partner"],
                    _d(r["order_datetime"]),
                    _d(r["delivery_date"]),
                    float(r["expected_amount"]),
                    float(r["remittance_amount"]),
                    float(r["amount_received"]),
                    float(r["difference"]),
                    r["remittance_reference"],
                    _d(r["remittance_date"]),
                    _d(r["bank_credit_date"]),
                    r["status_label"],
                    *(export_value(r[key]) for _header, key in BANK_EXPORT_COLUMNS),
                ]
            )
        ps = wb.create_sheet("Partner-wise")
        ps.append(
            [
                "Partner",
                "Delivered",
                "COD amount",
                "COD settled (courier-reported)",
                "COD bank confirmed",
                "Pending",
                "Short",
                "Excess",
                "Matched",
                "Unmatched lines",
                "Completion %",
            ]
        )
        for p in await self.report_partners(f):
            ps.append(
                [
                    p["partner"],
                    p["delivered_orders"],
                    float(p["expected_amount"]),
                    float(p["remittance_amount"]),
                    float(p["amount_received"]),
                    p["pending_orders"],
                    p["short_orders"],
                    p["excess_orders"],
                    p["matched_orders"],
                    p["unmatched_lines"],
                    _p(p["completion_percent"]),
                ]
            )
        ds = wb.create_sheet("Settlement-date-wise")
        ds.append(
            [
                "Date",
                "Lines",
                "COD settled, courier-reported (incl. unmatched lines)",
                "COD bank confirmed",
                "Unmatched lines",
            ]
        )
        for b in await self.report_settlement_dates(f):
            ds.append(
                [
                    b["date"],
                    b["lines"],
                    float(b["amount"]),
                    float(b["bank_confirmed_amount"]),
                    b["unmatched_lines"],
                ]
            )
        ag = wb.create_sheet("Pending ageing")
        ag.append(
            [
                "Age",
                "Pending orders",
                "Pending amount",
                "Short orders",
                "Short amount",
                "Awaiting bank orders",
                "COD settled awaiting bank confirmation",
            ]
        )
        for b in await self.report_ageing(f):
            ag.append(
                [
                    b["bucket"],
                    b["pending_orders"],
                    float(b["pending_amount"]),
                    b["short_orders"],
                    float(b["short_amount"]),
                    b["awaiting_bank_orders"],
                    float(b["awaiting_bank_amount"]),
                ]
            )
        un = wb.create_sheet("Unmatched")
        un.append(["Type", "Partner", "AWB", "Order ref", "Reference", "Amount", "Date", "Reason"])
        for u in await self._unmatched(f, shared["evidence"]):
            un.append(
                [
                    "Remittance line" if u["kind"] == "remittance_line" else "Bank credit",
                    u["partner"],
                    u["awb"],
                    u["order_ref"],
                    u["reference"],
                    float(u["amount"]),
                    _d(u["date"]),
                    u["reason"],
                ]
            )
        add_bank_summary_sheet(wb, (await self.summary(replace(f, q=None)))["kpis"])
        add_definitions_sheet(wb, MODULE)
        neutralise_formulas(wb)
        buffer = BytesIO()
        wb.save(buffer)
        return buffer.getvalue()


def _d(value: datetime | None) -> str | None:
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M") if value else None


def _p(value: float | None) -> float | None:
    return round(value, 1) if value is not None else None


def _enum_value(value: Any) -> str | None:
    return getattr(value, "value", value) if value is not None else None

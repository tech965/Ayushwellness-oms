"""Shiprocket shipping expenses and COD remittance payouts.

Sources, kept apart on purpose:

* Money movements: `ShiprocketWalletTransaction` (statement API, or an
  imported passbook report). The only figures in the expense KPIs.
* Shipment charge breakdown: `GET /orders/show/{id}`. Informational; never
  added to wallet totals (the same freight would be counted twice).
* COD payouts: an imported Shiprocket report. Shiprocket documents no API for
  payout amounts, deductions or CRF ids.
* Per-AWB remittance amounts: Module 6's COD report upload. Not stored again.

Bank matching here is a READ-ONLY comparison of a payout's UTR with bank
statement references. It creates no allocation, so it cannot change any
reconciliation figure. Cashfree settlements are not part of this module.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import IntegrationError, NotFoundError
from app.integrations.shiprocket.order_finance import parse_order_finance
from app.integrations.shiprocket.wallet import normalize_wallet_transaction
from app.models.auth import User
from app.models.cod_remittance import CodRemittance
from app.models.enums import PaymentType
from app.models.order import Order
from app.models.remittance_reconciliation import BankCredit, CodRemittanceLine
from app.models.shipment import Shipment
from app.models.shiprocket_finance import ShiprocketPayout, ShiprocketShipmentCharge
from app.models.shiprocket_wallet import ShiprocketWalletTransaction
from app.services import remittance_upload_parser as parser
from app.services.remittance_common import (
    DEFINITIONS,
    TOLERANCE,
    ZERO,
    audit,
    chunks,
    dec,
    drill_totals,
    drilldown_workbook,
    fmt_dt,
    money,
    norm_ref,
    paginate,
    store_upload,
)
from app.services.shiprocket_service import shiprocket_order_id
from app.services.shiprocket_wallet_service import (
    COUNTED_SOURCES,
    CREDIT_BACK_TYPES,
    EXPENSE_TYPES,
    REPORT_SOURCE,
    WalletService,
    find_same_movement,
)

MAX_ROWS = 50_000
MAX_ORDERS_PER_SYNC = 25


def _counted(t: ShiprocketWalletTransaction) -> bool:
    return t.status == "confirmed" and t.source in COUNTED_SOURCES


def _of_type(*types: str) -> Callable[[Any], bool]:
    return lambda t: _counted(t) and t.txn_type in types


def _reported(status: str) -> Callable[[Any], bool]:
    return lambda t: t.status == status and t.source in ("shiprocket_api", REPORT_SOURCE)


# (key, label, definition, belongs(txn), what it adds to the card)
EXPENSE_KPIS: tuple[tuple[str, str, str, Callable, Callable], ...] = (
    (
        "net_cost",
        "Net Shipping Cost",
        "All shipping debits minus reversals, refunds and other credits. Recharges are excluded.",
        lambda t: _counted(t) and t.txn_type in (*EXPENSE_TYPES, *CREDIT_BACK_TYPES),
        lambda t: t.amount if t.txn_type in EXPENSE_TYPES else -t.amount,
    ),
    ("freight", "Freight Charges", "Forward freight debited from the wallet.",
     _of_type("freight"), lambda t: t.amount),
    ("cod_charges", "COD Charges", "COD fees debited from the wallet.",
     _of_type("cod_charge"), lambda t: t.amount),
    ("rto", "RTO Freight", "Return-to-origin freight debited from the wallet.",
     _of_type("rto_freight"), lambda t: t.amount),
    ("weight", "Weight Discrepancy", "Extra charges for weight discrepancies.",
     _of_type("weight_discrepancy"), lambda t: t.amount),
    ("other_debits", "Other Debits", "Debits of any other kind.",
     _of_type("other_debit"), lambda t: t.amount),
    ("credits", "Reversals & Credits", "Freight / COD reversals, refunds and other credits.",
     _of_type(*CREDIT_BACK_TYPES), lambda t: t.amount),
    ("recharges", "Wallet Recharges", "Money added to the wallet. Not an expense.",
     _of_type("recharge"), lambda t: t.amount),
    ("pending", "Pending Transactions", "Reported as pending. In no expense total.",
     _reported("pending"), lambda t: t.amount),
    ("failed", "Failed Transactions", "Reported as failed. In no expense total.",
     _reported("failed"), lambda t: t.amount),
)  # fmt: skip

BANK_STATE_LABELS = {
    "matched": "Matched to bank",
    "differs": "Bank amount differs",
    "not_found": "Not found in bank statements",
    "no_utr": "No UTR yet",
    "no_bank_data": "No bank statement imported",
}


def _nonzero(key: str) -> Callable[[dict], bool]:
    return lambda p: p[key] is not None and p[key] != 0


def _state(name: str) -> Callable[[dict], bool]:
    return lambda p: p["bank_state"] == name


PAYOUT_KPIS: tuple[tuple[str, str, str, Callable, str], ...] = (
    ("net", "Net Remittance Reported", "Remittance amount of every imported payout.",
     lambda p: True, "net_amount"),
    ("cod_amount", "COD Amount in Payouts",
     "COD amount, only for payouts whose report states it.",
     lambda p: p["cod_amount"] is not None, "cod_amount"),
    ("freight_deducted", "Freight Deducted", "Freight the report shows deducted from payouts.",
     _nonzero("freight_deduction"), "freight_deduction"),
    ("other_deducted", "Other Deductions", "Other deductions the report shows.",
     _nonzero("other_deductions"), "other_deductions"),
    ("with_utr", "Payouts With UTR", "Payouts that have a bank reference (UTR).",
     lambda p: bool(p["utr"]), "net_amount"),
    ("without_utr", "Payouts Without UTR", "No bank reference reported yet.",
     _state("no_utr"), "net_amount"),
    ("bank_matched", "Matched to Bank",
     "Bank statement credits with the payout's UTR equal the amount reported under it "
     "(within Rs 1).", _state("matched"), "net_amount"),
    ("bank_differs", "Bank Amount Differs",
     "The UTR is on a bank statement, but the amounts differ.", _state("differs"), "net_amount"),
    ("not_in_bank", "Not Found in Bank",
     "A UTR that no imported bank statement line carries.", _state("not_found"), "net_amount"),
)  # fmt: skip

DEFINITIONS.update(
    {
        "sr_expenses": tuple((label, text) for _k, label, text, _s, _v in EXPENSE_KPIS),
        "sr_payouts": tuple((label, text) for _k, label, text, _s, _v in PAYOUT_KPIS),
    }
)


def _spec(table: tuple, key: str | None) -> tuple:
    for spec in table:
        if spec[0] == key:
            return spec
    raise NotFoundError(f"Unknown KPI '{key}'.")


def _txn_row(t: ShiprocketWalletTransaction) -> dict[str, Any]:
    return WalletService._row(t).model_dump()  # noqa: SLF001 - the shared row shape


class ShiprocketFinanceService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ---------------------------------------------------------------- sources

    async def sources(self) -> list[dict[str, Any]]:
        """What arrives by API and what needs a report, and whether it is on."""
        configured = bool(settings.SHIPROCKET_EMAIL and settings.SHIPROCKET_PASSWORD)
        statement = bool(settings.SHIPROCKET_WALLET_STATEMENT_PATH)

        def item(data, method, endpoint, state, note):
            return {
                "data": data,
                "method": method,
                "endpoint": endpoint,
                "state": state,
                "note": note,
            }

        on = "ready" if configured else "not_configured"
        return [
            item("Wallet balance", "api", "GET /account/details/wallet-balance", on,
                 "Refreshed every 30 minutes by the existing worker task."),
            item("Wallet transactions (freight, COD fee, RTO, recharges, reversals)", "api",
                 "GET /account/details/statement",
                 ("ready" if configured and statement else "not_enabled") if configured else on,
                 "Set SHIPROCKET_WALLET_STATEMENT_PATH=/account/details/statement after "
                 "checking it with the wallet probe. A passbook report can be imported "
                 "instead."),
            item("Freight and other charges per shipment", "api",
                 "GET /orders/show/{order_id} (awb_data.charges)", on,
                 "One request per order, run on demand. Needs a stored Shiprocket order id "
                 "and Orders permission for the API user. Shown separately from wallet "
                 "totals."),
            item("COD UTR, remittance date and status per order", "api",
                 "GET /orders/show/{order_id} (remittance_utr/date/status)", on,
                 "No amount is provided, so these never count as a remitted amount."),
            item("COD payouts: CRF id, amounts, deductions, status", "report",
                 None, "report_only",
                 "No Shiprocket API documents this. Import the report from the Shiprocket "
                 "dashboard."),
            item("COD remittance amount per AWB", "report", None, "report_only",
                 "Import under COD Remittance Reconciliation (Module 6), so amounts are "
                 "stored once."),
            item("Shiprocket bills / freight invoices", "not_available", None, "not_available",
                 "No documented API; /orders/print/invoice is the customer invoice. Not "
                 "imported."),
            item("Weight discrepancy records", "not_ingested", "GET /billing/discrepancy",
                 "not_ingested",
                 "Listed by Shiprocket, but its row format is not documented, so it is not "
                 "imported. Discrepancy debits still arrive in wallet transactions."),
        ]  # fmt: skip

    # ---------------------------------------------------------------- expenses

    async def _transactions(self, filters: dict[str, Any]) -> list[ShiprocketWalletTransaction]:
        stmt = WalletService(self.session)._filtered(  # noqa: SLF001 - one filter definition
            q=filters.get("q"),
            date_from=filters.get("date_from"),
            date_to=filters.get("date_to"),
            txn_type=filters.get("txn_type"),
            direction=None,
            courier=filters.get("courier"),
            status=filters.get("status"),
        )
        T = ShiprocketWalletTransaction
        return list(
            (
                await self.session.execute(stmt.order_by(T.txn_date.desc(), T.id).limit(MAX_ROWS))
            ).scalars()
        )

    async def expense_summary(self, filters: dict[str, Any]) -> dict[str, Any]:
        rows = await self._transactions(filters)
        cards = []
        for key, label, text, belongs, value in EXPENSE_KPIS:
            found = [t for t in rows if belongs(t)]
            cards.append(
                {
                    "key": key,
                    "label": label,
                    "definition": text,
                    "count": len(found),
                    "amount": money(sum((value(t) for t in found), ZERO)),
                }
            )
        state = await WalletService(self.session).state()
        couriers = sorted({t.courier_name for t in rows if t.courier_name})
        return {
            "cards": cards,
            "wallet": state.model_dump(),
            "transaction_count": len(rows),
            "couriers": couriers,
        }

    async def expense_drilldown(self, filters: dict[str, Any], kpi: str) -> tuple[list[dict], dict]:
        _key, label, _text, belongs, value = _spec(EXPENSE_KPIS, kpi)

        async def pick(chosen: dict[str, Any]) -> list[dict]:
            return [
                {**_txn_row(t), "contribution": money(value(t))}
                for t in await self._transactions(chosen)
                if belongs(t)
            ]

        rows = await pick(filters)
        base = await pick({**filters, "q": None}) if filters.get("q") else rows
        return rows, drill_totals(label, rows, [r["contribution"] for r in base])

    async def export_expenses(self, filters: dict[str, Any], kpi: str) -> bytes:
        rows, totals = await self.expense_drilldown(filters, kpi)
        columns = [
            ("Date", lambda r: fmt_dt(r["txn_date"])),
            ("Transaction ID", lambda r: r["transaction_ref"]),
            ("Type", lambda r: r["txn_type"]),
            ("Direction", lambda r: r["direction"]),
            ("Amount", lambda r: float(r["amount"])),
            ("Order", lambda r: r["order_number"]),
            ("AWB", lambda r: r["awb"]),
            ("Courier", lambda r: r["courier_name"]),
            ("Description", lambda r: r["description"]),
            ("Status", lambda r: r["status"]),
            ("Source", lambda r: r["source"]),
            (f"Counts toward card: {totals['measure']}", lambda r: float(r["contribution"])),
        ]
        return drilldown_workbook(columns, rows, totals, module="sr_expenses")

    # -------------------------------------------------------- shipment charges

    async def charges(
        self, *, q: str | None, page_params: Any
    ) -> tuple[list[dict], int, dict[str, int]]:
        C = ShiprocketShipmentCharge
        found = list(
            (await self.session.execute(select(C).order_by(C.fetched_at.desc(), C.awb))).scalars()
        )
        coverage = {
            "orders_checked": len(found),
            "with_charges": sum(1 for c in found if c.has_charges),
        }
        rows = [c for c in found if c.has_charges]
        if q and q.strip():
            term = q.strip().casefold()
            rows = [
                c
                for c in rows
                if term in c.awb.casefold() or term in (c.order_number or "").casefold()
            ]
        fields = (
            "awb",
            "order_number",
            "courier_name",
            "zone",
            "freight_charges",
            "cod_charges",
            "applied_weight_amount",
            "charged_weight_amount",
            "applied_weight_amount_rto",
            "charged_weight_amount_rto",
            "applied_weight",
            "charged_weight",
            "fetched_at",
        )
        page, total = paginate([{f: getattr(c, f) for f in fields} for c in rows], page_params)
        return page, total, coverage

    # ------------------------------------------------------- order finance sync

    async def _sync_candidates(self, limit: int) -> list[tuple[Shipment, Order, str]]:
        """Shipments with a KNOWN Shiprocket order id, never-checked first.
        The id comes from the OMS's own create response, or from a wallet
        statement row for the same AWB. It is never guessed."""
        C = ShiprocketShipmentCharge
        found = (
            await self.session.execute(
                select(Shipment, Order)
                .join(Order, Order.id == Shipment.order_id)
                .outerjoin(C, C.awb == Shipment.awb)
                .where(Shipment.awb.is_not(None))
                .order_by(C.fetched_at.asc().nulls_first(), Shipment.created_at.desc())
                .limit(2000)
            )
        ).all()
        by_awb: dict[str, str] = {}
        T = ShiprocketWalletTransaction
        for part in chunks([s.awb for s, _o in found]):
            for awb, raw in (
                await self.session.execute(
                    select(T.awb, T.raw_external_payload).where(T.awb.in_(list(part)))
                )
            ).all():
                if isinstance(raw, dict) and str(raw.get("order_id") or "").isdigit():
                    by_awb[awb] = str(raw["order_id"])
        out = []
        for shipment, order in found:
            order_id = shiprocket_order_id(shipment) or by_awb.get(shipment.awb)
            if order_id:
                out.append((shipment, order, str(order_id)))
            if len(out) >= limit:
                break
        return out

    async def sync_order_finance(
        self, adapter: Any, *, limit: int, user: User | None
    ) -> dict[str, Any]:
        limit = max(1, min(limit, MAX_ORDERS_PER_SYNC))
        candidates = await self._sync_candidates(limit)
        user_id = user.id if user else None
        result: dict[str, Any] = {
            "status": "ok",
            "candidates": len(candidates),
            "checked": 0,
            "with_charges": 0,
            "with_remittance_reference": 0,
            "unreadable": 0,
            "failed": 0,
            "message": None,
        }
        for shipment, order, order_id in candidates:
            try:
                body = await adapter.get_order(order_id)
            except IntegrationError as exc:
                details = exc.details or {}
                if details.get("orders_show_blocked") or details.get("error_type") in (
                    "authorization_error",
                    "authentication_error",
                ):
                    result["status"] = "access_denied"
                    result["message"] = (
                        "Shiprocket refused GET /orders/show for this API user. Grant it the "
                        "Orders module in Shiprocket, then restart the service."
                    )
                    break
                result["failed"] += 1
                continue
            parsed = parse_order_finance(body)
            if parsed is None:
                result["unreadable"] += 1
                continue
            result["checked"] += 1
            result["with_charges"] += int(parsed["has_charges"])
            await self._store_charges(shipment, order, order_id, parsed)
            if await self._store_order_remittance(shipment, order, order_id, parsed):
                result["with_remittance_reference"] += 1
        if result["status"] == "ok" and result["failed"]:
            result["status"] = "partial"
        audit_user = await self.session.get(User, user_id) if user_id else None
        await audit(
            self.session,
            audit_user,
            "shiprocket.order_finance.sync",
            "shiprocket_shipment_charge",
            "bulk",
            new={k: v for k, v in result.items() if k != "message"},
        )
        await self.session.commit()
        return result

    async def _store_charges(
        self, shipment: Shipment, order: Order, order_id: str, parsed: dict
    ) -> None:
        row = await self.session.scalar(
            select(ShiprocketShipmentCharge).where(ShiprocketShipmentCharge.awb == shipment.awb)
        )
        if row is None:
            row = ShiprocketShipmentCharge(awb=shipment.awb, has_charges=False)
            self.session.add(row)
        row.shiprocket_order_id = order_id
        row.order_number = order.order_number
        row.zone = parsed["zone"] or row.zone
        for name, value in parsed["charges"].items():
            if value is not None:  # a blank answer never erases a reported figure
                setattr(row, name, value)
        row.has_charges = row.has_charges or parsed["has_charges"]
        row.fetched_at = datetime.now(UTC)
        row.raw_external_payload = parsed["raw"]
        await self.session.flush()

    async def _store_order_remittance(
        self, shipment: Shipment, order: Order, order_id: str, parsed: dict
    ) -> bool:
        """Records only the UTR / status / date. No amount is documented, so
        no amount is written and Module 6 calculates exactly as before."""
        utr, status = parsed["remittance_utr"], parsed["remittance_status"]
        paid_on = parsed["remittance_date"]
        if order.payment_type != PaymentType.COD or not (utr or status or paid_on):
            return False
        row = await self.session.scalar(
            select(CodRemittance).where(CodRemittance.awb == shipment.awb)
        )
        if row is None:
            row = CodRemittance(awb=shipment.awb, shiprocket_order_id=order_id)
            self.session.add(row)
        row.remittance_reference = row.remittance_reference or utr
        row.settlement_date = row.settlement_date or paid_on
        row.remittance_status = status or row.remittance_status
        row.synced_at = datetime.now(UTC)
        await self.session.flush()
        return bool(utr)

    async def order_remittance_status(
        self, *, q: str | None, page_params: Any
    ) -> tuple[list[dict], int]:
        R = CodRemittance
        found = list(
            (
                await self.session.execute(
                    select(R)
                    .where(
                        R.remittance_reference.is_not(None)
                        | R.remittance_status.is_not(None)
                        | R.settlement_date.is_not(None)
                    )
                    .order_by(R.settlement_date.desc(), R.awb)
                    .limit(MAX_ROWS)
                )
            ).scalars()
        )
        rows = [
            {
                "awb": r.awb,
                "utr": r.remittance_reference,
                "status": r.remittance_status,
                "remittance_date": r.settlement_date,
                "remitted_amount": r.remitted_amount,
            }
            for r in found
        ]
        if q and q.strip():
            term = q.strip().casefold()
            rows = [
                r
                for r in rows
                if term in r["awb"].casefold() or term in (r["utr"] or "").casefold()
            ]
        return paginate(rows, page_params)

    # ----------------------------------------------------------------- payouts

    async def _bank_by_reference(self) -> tuple[dict[str, Decimal], bool]:
        rows = (await self.session.execute(select(BankCredit.reference, BankCredit.amount))).all()
        totals: dict[str, Decimal] = defaultdict(lambda: ZERO)
        for reference, amount in rows:
            if norm_ref(reference):
                totals[norm_ref(reference)] += dec(amount)
        return totals, bool(rows)

    async def _payouts(self, filters: dict[str, Any]) -> list[dict[str, Any]]:
        everything = list(
            (await self.session.execute(select(ShiprocketPayout).limit(MAX_ROWS))).scalars()
        )
        bank, has_bank = await self._bank_by_reference()
        # Payouts sharing a UTR are compared together, so one bank credit can
        # never make two payouts each look fully matched.
        reported: dict[str, Decimal] = defaultdict(lambda: ZERO)
        for p in everything:
            if norm_ref(p.utr):
                reported[norm_ref(p.utr)] += dec(p.net_amount)
        rows = []
        for p in everything:
            ref = norm_ref(p.utr)
            credited = bank.get(ref) if ref else None
            if not ref:
                state = "no_utr"
            elif not has_bank:
                state = "no_bank_data"
            elif credited is None:
                state = "not_found"
            else:
                state = "matched" if abs(credited - reported[ref]) <= TOLERANCE else "differs"
            deductions = (p.freight_deduction, p.other_deductions)
            unexplained = None
            if p.cod_amount is not None and all(d is not None for d in deductions):
                unexplained = money(p.cod_amount - sum(deductions, ZERO) - p.net_amount)
            rows.append(
                {
                    "id": p.id,
                    "crf_id": p.crf_id,
                    "utr": p.utr,
                    "payout_date": p.payout_date,
                    "status": p.status,
                    "cod_amount": p.cod_amount,
                    "freight_deduction": p.freight_deduction,
                    "other_deductions": p.other_deductions,
                    "net_amount": p.net_amount,
                    "unexplained_difference": unexplained,
                    "bank_amount": money(credited) if credited is not None else None,
                    "bank_reported_under_utr": money(reported[ref]) if ref else None,
                    "bank_state": state,
                    "bank_state_label": BANK_STATE_LABELS[state],
                }
            )
        date_from, date_to = filters.get("date_from"), filters.get("date_to")
        status = (filters.get("status") or "").casefold()
        term = (filters.get("q") or "").strip().casefold()
        out = [
            r
            for r in rows
            # Undated payouts are never hidden by a date range.
            if not (r["payout_date"] and date_from and r["payout_date"] < date_from)
            and not (r["payout_date"] and date_to and r["payout_date"] > date_to)
            and (not status or (r["status"] or "").casefold() == status)
            and (not filters.get("bank_state") or r["bank_state"] == filters["bank_state"])
            and (
                not term
                or term in (r["crf_id"] or "").casefold()
                or term in (r["utr"] or "").casefold()
            )
        ]
        floor = datetime.min.replace(tzinfo=UTC)
        out.sort(key=lambda r: (r["payout_date"] or floor, str(r["id"])), reverse=True)
        return out

    async def payout_summary(self, filters: dict[str, Any]) -> dict[str, Any]:
        rows = await self._payouts(filters)
        cards = []
        for key, label, text, belongs, field in PAYOUT_KPIS:
            found = [r for r in rows if belongs(r)]
            cards.append(
                {
                    "key": key,
                    "label": label,
                    "definition": text,
                    "count": len(found),
                    "amount": money(sum((r[field] or ZERO for r in found), ZERO)),
                }
            )
        every = await self._payouts({})
        return {
            "cards": cards,
            "payout_count": len(rows),
            "statuses": sorted({r["status"] for r in every if r["status"]}),
            "bank_data_available": (await self._bank_by_reference())[1],
        }

    async def payout_drilldown(self, filters: dict[str, Any], kpi: str) -> tuple[list[dict], dict]:
        _key, label, _text, belongs, field = _spec(PAYOUT_KPIS, kpi)

        async def pick(chosen: dict[str, Any]) -> list[dict]:
            return [
                {**r, "contribution": money(r[field] or ZERO)}
                for r in await self._payouts(chosen)
                if belongs(r)
            ]

        rows = await pick(filters)
        base = await pick({**filters, "q": None}) if filters.get("q") else rows
        return rows, drill_totals(label, rows, [r["contribution"] for r in base])

    async def export_payouts(self, filters: dict[str, Any], kpi: str) -> bytes:
        rows, totals = await self.payout_drilldown(filters, kpi)

        def num(value: Any) -> float | None:
            return float(value) if value is not None else None

        columns = [
            ("CRF / payout ID", lambda r: r["crf_id"]),
            ("UTR", lambda r: r["utr"]),
            ("Payout date", lambda r: fmt_dt(r["payout_date"])),
            ("Status (as reported)", lambda r: r["status"]),
            ("COD amount", lambda r: num(r["cod_amount"])),
            ("Freight deducted", lambda r: num(r["freight_deduction"])),
            ("Other deductions", lambda r: num(r["other_deductions"])),
            ("Remittance amount", lambda r: num(r["net_amount"])),
            ("COD - deductions - remittance", lambda r: num(r["unexplained_difference"])),
            ("Bank amount under UTR", lambda r: num(r["bank_amount"])),
            ("Bank comparison", lambda r: r["bank_state_label"]),
            (f"Counts toward card: {totals['measure']}", lambda r: float(r["contribution"])),
        ]
        return drilldown_workbook(columns, rows, totals, module="sr_payouts")

    async def payout_detail(self, payout_id: uuid.UUID) -> dict[str, Any]:
        record = next((r for r in await self._payouts({}) if r["id"] == payout_id), None)
        if record is None:
            raise NotFoundError("Payout not found.")
        refs = [ref for ref in (norm_ref(record["utr"]), norm_ref(record["crf_id"])) if ref]
        credits = []
        if norm_ref(record["utr"]):
            credits = list(
                (
                    await self.session.execute(
                        select(BankCredit).where(
                            func.lower(func.trim(BankCredit.reference)) == norm_ref(record["utr"])
                        )
                    )
                ).scalars()
            )
        lines = []
        if refs:
            lines = list(
                (
                    await self.session.execute(
                        select(CodRemittanceLine).where(
                            func.lower(func.trim(CodRemittanceLine.remittance_reference)).in_(refs)
                        )
                    )
                ).scalars()
            )
        return {
            "payout": record,
            "bank_credits": [
                {"reference": c.reference, "amount": money(dec(c.amount)),
                 "credit_date": c.credit_date, "narration": c.narration}
                for c in credits
            ],
            "awb_lines": [
                {"awb": line.awb, "order_ref": line.order_ref,
                 "reference": line.remittance_reference,
                 "amount": money(dec(line.remittance_amount)),
                 "matched_to_order": line.matched_order_id is not None}
                for line in lines
            ],
            "awb_lines_total": money(sum((dec(x.remittance_amount) for x in lines), ZERO)),
        }  # fmt: skip

    # ----------------------------------------------------------------- imports

    async def import_payouts(
        self, *, filename: str, content_type: str | None, data: bytes, user: User,
        column_map: dict[str, str] | None = None,
    ) -> dict[str, Any]:  # fmt: skip
        parsed = parser.parse_shiprocket_payouts(filename, data, column_map)
        record = await store_upload(
            self.session, module="sr_payout", source_name="Shiprocket", filename=filename,
            content_type=content_type, data=data, user=user,
        )  # fmt: skip
        added = updated = duplicates = 0
        conflicts: list[dict[str, Any]] = []
        for row in parsed.rows:
            existing = await self._existing_payout(row)
            if existing is None:
                self.session.add(ShiprocketPayout(file_id=record.id, **row))
                await self.session.flush()
                added += 1
                continue
            # A later report may fill blanks or move the status on. It may never
            # change money or an already reported UTR.
            clash = [
                name
                for name in ("net_amount", "cod_amount", "freight_deduction", "other_deductions")
                if row[name] is not None
                and getattr(existing, name) is not None
                and money(dec(getattr(existing, name))) != money(row[name])
            ]
            if existing.utr and row["utr"] and norm_ref(existing.utr) != norm_ref(row["utr"]):
                clash.append("utr")
            if clash:
                conflicts.append({"payout": existing.crf_id or existing.utr, "differs_in": clash})
                continue
            changed = False
            for name in ("crf_id", "utr", "payout_date", "cod_amount", "freight_deduction",
                         "other_deductions"):  # fmt: skip
                if getattr(existing, name) is None and row[name] is not None:
                    setattr(existing, name, row[name])
                    changed = True
            if row["status"] and row["status"] != existing.status:
                existing.status = row["status"]
                changed = True
            updated += int(changed)
            duplicates += int(not changed)
        record.row_count = parsed.total_rows
        record.accepted_count = added
        record.duplicate_count = duplicates
        record.rejected_count = len(parsed.errors) + len(conflicts)
        record.row_errors = (parsed.errors + conflicts)[:100]
        summary = {"added": added, "updated": updated, "duplicates_skipped": duplicates,
                   "conflicts": len(conflicts), "rejected": len(parsed.errors)}  # fmt: skip
        await audit(self.session, user, "shiprocket.payout.import", "settlement_file", record.id,
                    new={"file": filename, **summary})  # fmt: skip
        await self.session.commit()
        return {
            **summary,
            "rows_read": parsed.total_rows,
            "row_errors": parsed.errors[:50],
            "conflict_rows": conflicts[:50],
            "column_mapping": parsed.column_mapping,
            "ignored_columns": parsed.ignored_columns,
        }

    async def _existing_payout(self, row: dict[str, Any]) -> ShiprocketPayout | None:
        P = ShiprocketPayout
        found = await self.session.scalar(select(P).where(P.dedupe_key == row["dedupe_key"]))
        if found is not None or not row["utr"]:
            return found
        same_utr = list(
            (
                await self.session.execute(
                    select(P).where(func.lower(func.trim(P.utr)) == norm_ref(row["utr"]))
                )
            ).scalars()
        )
        # The same payout reported once by UTR only and once with its CRF id.
        for candidate in same_utr:
            one_has_no_crf = not candidate.crf_id or not row["crf_id"]
            if one_has_no_crf and money(dec(candidate.net_amount)) == money(row["net_amount"]):
                return candidate
        return None

    async def import_wallet_report(
        self, *, filename: str, content_type: str | None, data: bytes, user: User,
        column_map: dict[str, str] | None = None,
    ) -> dict[str, Any]:  # fmt: skip
        parsed = parser.parse_shiprocket_wallet(filename, data, column_map)
        record = await store_upload(
            self.session, module="sr_wallet", source_name="Shiprocket", filename=filename,
            content_type=content_type, data=data, user=user,
        )  # fmt: skip
        T = ShiprocketWalletTransaction
        added = duplicates = 0
        errors = list(parsed.errors)
        consumed: set[uuid.UUID] = set()
        for raw in parsed.rows:
            line_no = raw.pop("_row")
            values = normalize_wallet_transaction(raw)
            if values is None:
                errors.append(
                    {"row": line_no,
                     "error": "Needs exactly one of a debit or a credit amount."}
                )  # fmt: skip
                continue
            existing = await self.session.scalar(
                select(T).where(T.dedup_key == values["dedup_key"])
            ) or await find_same_movement(self.session, values, consumed=consumed)
            if existing is not None:
                consumed.add(existing.id)
                duplicates += 1
                continue
            row = ShiprocketWalletTransaction(
                **values, source=REPORT_SOURCE, synced_at=datetime.now(UTC),
                raw_external_payload=raw,
            )  # fmt: skip
            self.session.add(row)
            await self.session.flush()
            consumed.add(row.id)
            added += 1
        record.row_count = parsed.total_rows
        record.accepted_count = added
        record.duplicate_count = duplicates
        record.rejected_count = len(errors)
        record.row_errors = errors[:100]
        summary = {"added": added, "updated": 0, "duplicates_skipped": duplicates,
                   "conflicts": 0, "rejected": len(errors)}  # fmt: skip
        await audit(self.session, user, "shiprocket.wallet_report.import", "settlement_file",
                    record.id, new={"file": filename, **summary})  # fmt: skip
        await self.session.commit()
        return {
            **summary,
            "rows_read": parsed.total_rows,
            "row_errors": errors[:50],
            "conflict_rows": [],
            "column_mapping": parsed.column_mapping,
            "ignored_columns": parsed.ignored_columns,
        }

"""Revenue split (COD/Prepaid x Fulfilled/Unfulfilled) and COD remittance
reconciliation.

Order content comes from the existing local Shopify-synced `orders` rows
(via `OrderRepository.search_query`, so search/date/payment/fulfillment
filters behave exactly like the Orders page). Shipment/tracking comes from
the existing `shipments` rows. Collection/remittance comes from
`cod_remittances`, filled by `sync_remittances` through the existing
Shiprocket client.

Honesty rules: a delivered COD shipment is NOT treated as collected or
remitted -- those figures exist only where Shiprocket reported them. And
"reported remitted" (Shiprocket's claim) is kept apart from "bank
verified" (a human matched it to a bank credit).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from io import BytesIO
from typing import Any

from openpyxl import Workbook
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import NotFoundError, ValidationError
from app.core.logging import get_logger
from app.integrations.shiprocket.adapter import ShiprocketAdapter
from app.integrations.shiprocket.remittance import normalize_cod_remittance
from app.models.auth import User
from app.models.cod_remittance import CodRemittance
from app.models.enums import CancellationStatus, FulfillmentStatus, OrderStatus, PaymentType
from app.models.order import Order
from app.models.order_workflow import OrderWorkflow
from app.models.remittance_reconciliation import BankCredit
from app.models.shipment import Shipment
from app.models.shipment_ref import ShipmentRef
from app.repositories.order import OrderRepository
from app.services.audit_service import AuditService
from app.services.live_collect import collect_orders
from app.services.live_orders import get_reader
from app.services.order_ref import get_workflow_by_external_ids, ref_id_for
from app.services.remittance_common import load_ledger, lock_bank_credits

logger = get_logger(__name__)

ZERO = Decimal("0")
MAX_EXPORT_ROWS = 10_000
SYNC_MAX_PAGES = 50
TRACKING_URL = "https://shiprocket.co/tracking/{awb}"


class Bucket(BaseModel):
    orders: int = 0
    amount: Decimal = ZERO


class PaymentSplit(BaseModel):
    total: Bucket
    fulfilled: Bucket
    unfulfilled: Bucket
    partial: Bucket


class CodFinancials(BaseModel):
    expected_amount: Decimal  # order totals of COD orders that have a shipment
    collected_amount: Decimal  # as reported by Shiprocket
    remitted_amount: Decimal  # as reported by Shiprocket
    pending_remittance: Decimal  # reported collected - reported remitted, per AWB, floored at 0
    bank_verified_amount: Decimal  # human-verified bank credits only
    shipped_orders: int
    orders_with_shiprocket_data: int
    orders_without_shiprocket_data: int
    remittance_last_synced_at: datetime | None


class RevenueSummary(BaseModel):
    # "live" = Shopify read live (bounded); "local" = stored orders.
    data_source: str = "local"
    complete: bool = True
    warnings: list[str] = []
    total_revenue: Decimal
    cod: PaymentSplit
    prepaid: PaymentSplit
    cod_financials: CodFinancials


class OrderRow(BaseModel):
    id: uuid.UUID
    order_number: str
    order_datetime: datetime
    customer_name: str | None
    customer_phone: str | None
    customer_email: str | None
    amount: Decimal
    currency: str
    payment_type: str
    payment_status: str
    fulfillment_status: str
    shipment_status: str | None
    courier_name: str | None
    awb: str | None
    tracking_url: str | None
    # COD-only; all None for prepaid.
    expected_cod_amount: Decimal | None = None
    collected_amount: Decimal | None = None
    remitted_amount: Decimal | None = None
    pending_remittance: Decimal | None = None
    remittance_status: str | None = None
    settlement_date: datetime | None = None
    settlement_reference: str | None = None
    # "no_data" = never reported by Shiprocket; "reported" = Shiprocket data present.
    financial_data: str | None = None
    bank_verified: bool | None = None
    bank_credit_amount: Decimal | None = None
    bank_credit_reference: str | None = None
    bank_credited_on: datetime | None = None


@dataclass
class _Input:
    """One order, source-agnostic (local DB row or live Shopify order)."""

    id: uuid.UUID
    order_number: str
    order_datetime: datetime
    customer_name: str | None
    customer_phone: str | None
    customer_email: str | None
    amount: Decimal
    currency: str
    payment_type: PaymentType
    payment_status: Any
    fulfillment_status: Any
    cancelled: bool
    awb: str | None
    courier_name: str | None
    shipment_status: str | None


def _dec(value: Decimal | None) -> Decimal:
    return value if value is not None else ZERO


def _pending(rem: CodRemittance | None) -> Decimal | None:
    if rem is None or rem.collected_amount is None:
        return None
    return max(rem.collected_amount - _dec(rem.remitted_amount), ZERO)


def _enum_value(v: Any) -> str:
    return v.value if hasattr(v, "value") else str(v)


class CodReconciliationService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.orders = OrderRepository(session)

    # ---------------------------------------------------------------- sync

    async def sync_remittances(self, adapter: ShiprocketAdapter) -> dict[str, int]:
        """Upserts by AWB, so re-running never duplicates rows. Overwrites
        only Shiprocket-reported columns; never touches `bank_*`, and never
        blanks a stored value with a missing one."""
        now = datetime.now(UTC)
        created = updated = skipped = seen = 0
        page = 1
        while page <= SYNC_MAX_PAGES:
            records, has_more = await adapter.fetch_cod_remittances(page=page)
            seen += len(records)
            for raw in records:
                values = normalize_cod_remittance(raw)
                if values is None:
                    skipped += 1
                    continue
                existing = (
                    await self.session.execute(
                        select(CodRemittance).where(CodRemittance.awb == values["awb"])
                    )
                ).scalar_one_or_none()
                if existing is None:
                    self.session.add(
                        CodRemittance(**values, synced_at=now, raw_external_payload=raw)
                    )
                    created += 1
                else:
                    for key, value in values.items():
                        if value is not None:
                            setattr(existing, key, value)
                    existing.synced_at = now
                    existing.raw_external_payload = raw
                    updated += 1
                await self.session.flush()
            if not has_more:
                break
            page += 1
        await self.session.commit()
        if seen and created + updated == 0:
            raise ValidationError(
                f"Shiprocket returned {seen} records but none had a recognisable AWB. "
                "The remittance response format is not what this sync expects; "
                "run /revenue-reconciliation/shiprocket-probe and adjust the normalizer."
            )
        logger.info("cod_remittance_sync_done", created=created, updated=updated, skipped=skipped)
        return {"created": created, "updated": updated, "skipped": skipped}

    async def verify_bank_credit(
        self,
        awb: str,
        *,
        amount: Decimal,
        reference: str,
        credited_on: datetime,
        reason: str,
        user: User | None,
    ) -> CodRemittance:
        """ADMIN manual verification of a Shiprocket-reported remittance. Reason,
        bank reference and amount are mandatory and audit logged. The amount
        can exceed neither the reported remittance nor, when a bank statement
        has that reference, what is still free on those credits.
        """
        if amount <= 0 or not reference.strip():
            raise ValidationError("Bank credit needs a positive amount and a bank reference.")
        if len(reason.strip()) < 3:
            raise ValidationError("A reason is required for a manual bank verification.")
        statement_ids = list(
            (
                await self.session.execute(
                    select(BankCredit.id).where(
                        func.lower(func.trim(BankCredit.reference)) == reference.strip().lower()
                    )
                )
            )
            .scalars()
            .all()
        )
        await lock_bank_credits(self.session, statement_ids)
        rem = (
            await self.session.execute(select(CodRemittance).where(CodRemittance.awb == awb))
        ).scalar_one_or_none()
        if rem is None:
            raise NotFoundError(f"No Shiprocket remittance record for AWB {awb}.")
        remitted = rem.remitted_amount or ZERO
        if amount > remitted:
            raise ValidationError(
                f"The amount cannot exceed the reported remittance of {remitted}."
            )
        if statement_ids:
            ledger = await load_ledger(self.session, exclude_awb=awb)
            free = sum((ledger.free(c) for c in ledger.credits if c.id in statement_ids), ZERO)
            if amount > free:
                raise ValidationError(
                    f"Only {free} of the bank credit with this reference is still unallocated."
                )
        previous = (
            {
                "amount": str(rem.bank_credit_amount),
                "bank_reference": rem.bank_credit_reference,
                "reason": rem.bank_verification_reason,
            }
            if rem.bank_verified_at
            else None
        )
        rem.bank_credit_amount = amount
        rem.bank_credit_reference = reference.strip()[:128]
        rem.bank_credited_on = credited_on
        rem.bank_verified_at = datetime.now(UTC)
        rem.bank_verified_by = user.id if user else None
        rem.bank_verification_reason = reason.strip()[:255]
        await AuditService(self.session).record(
            user=user,
            action="remittance.cod.bank_verification",
            entity_type="cod_remittance",
            entity_id=str(rem.id),
            previous_value=previous,
            new_value={
                "verification": "manually_verified",
                "awb": awb,
                "amount": str(amount),
                "bank_reference": reference.strip(),
                "reason": reason.strip(),
                "credited_on": credited_on.isoformat(),
            },
        )
        await self.session.commit()
        return rem

    # ------------------------------------------------------------- summary

    @staticmethod
    def _live_orders(stmt, date_from, date_to):  # noqa: ANN001, ANN205
        stmt = stmt.where(
            Order.status != OrderStatus.CANCELLED,
            Order.cancellation_status != CancellationStatus.CANCELLED,
        )
        if date_from:
            stmt = stmt.where(Order.order_datetime >= date_from)
        if date_to:
            stmt = stmt.where(Order.order_datetime <= date_to)
        return stmt

    async def summary(
        self, *, date_from: datetime | None, date_to: datetime | None
    ) -> RevenueSummary:
        if settings.SHOPIFY_LIVE_MODE:
            return await self._live_summary(date_from=date_from, date_to=date_to)
        grouped = self._live_orders(
            select(
                Order.payment_type,
                Order.fulfillment_status,
                func.count(Order.id),
                func.coalesce(func.sum(Order.total_amount), 0),
            ).group_by(Order.payment_type, Order.fulfillment_status),
            date_from,
            date_to,
        )
        splits: dict[str, dict[str, Bucket]] = {
            t: {k: Bucket() for k in ("total", "fulfilled", "unfulfilled", "partial")}
            for t in ("cod", "prepaid")
        }
        for ptype, fstatus, count, amount in (await self.session.execute(grouped)).all():
            key = _enum_value(ptype)
            if key not in splits:
                continue  # "other" payment types are outside this COD/Prepaid split
            for bucket in (splits[key]["total"], splits[key].get(_enum_value(fstatus))):
                if bucket is not None:
                    bucket.orders += count
                    bucket.amount += Decimal(amount)

        # Expected COD = order totals of COD orders that actually have a shipment.
        has_ship = select(Shipment.id).where(Shipment.order_id == Order.id).exists()
        shipped = (
            await self.session.execute(
                self._live_orders(
                    select(
                        func.count(Order.id), func.coalesce(func.sum(Order.total_amount), 0)
                    ).where(Order.payment_type == PaymentType.COD, has_ship),
                    date_from,
                    date_to,
                )
            )
        ).one()

        rem_rows = (
            (
                await self.session.execute(
                    self._live_orders(
                        select(CodRemittance)
                        .join(Shipment, Shipment.awb == CodRemittance.awb)
                        .join(Order, Order.id == Shipment.order_id)
                        .where(Order.payment_type == PaymentType.COD),
                        date_from,
                        date_to,
                    )
                )
            )
            .scalars()
            .all()
        )
        collected = sum((_dec(r.collected_amount) for r in rem_rows), ZERO)
        remitted = sum((_dec(r.remitted_amount) for r in rem_rows), ZERO)
        pending = sum((_pending(r) or ZERO for r in rem_rows), ZERO)
        verified = sum((_dec(r.bank_credit_amount) for r in rem_rows if r.bank_verified_at), ZERO)
        last_sync = (
            await self.session.execute(select(func.max(CodRemittance.synced_at)))
        ).scalar_one()
        with_data = len({r.awb for r in rem_rows})
        return RevenueSummary(
            total_revenue=splits["cod"]["total"].amount + splits["prepaid"]["total"].amount,
            cod=PaymentSplit(**splits["cod"]),
            prepaid=PaymentSplit(**splits["prepaid"]),
            cod_financials=CodFinancials(
                expected_amount=Decimal(shipped[1]),
                collected_amount=collected,
                remitted_amount=remitted,
                pending_remittance=pending,
                bank_verified_amount=verified,
                shipped_orders=shipped[0],
                orders_with_shiprocket_data=with_data,
                orders_without_shiprocket_data=max(shipped[0] - with_data, 0),
                remittance_last_synced_at=last_sync,
            ),
        )

    # -------------------------------------------------------------- orders

    @staticmethod
    def _local_inputs(orders: list[Order]) -> list[_Input]:
        out: list[_Input] = []
        for o in orders:
            ship = max(o.shipments, key=lambda s: s.created_at) if o.shipments else None
            out.append(
                _Input(
                    id=o.id,
                    order_number=o.order_number,
                    order_datetime=o.order_datetime,
                    customer_name=o.customer.full_name if o.customer else None,
                    customer_phone=o.customer.phone if o.customer else None,
                    customer_email=o.customer.email if o.customer else None,
                    amount=o.total_amount,
                    currency=o.currency,
                    payment_type=o.payment_type,
                    payment_status=o.payment_status,
                    fulfillment_status=o.fulfillment_status,
                    cancelled=False,
                    awb=ship.awb if ship else None,
                    courier_name=ship.courier.name if ship and ship.courier else None,
                    shipment_status=_enum_value(ship.current_status) if ship else None,
                )
            )
        return out

    async def _rows(self, orders: list[Order]) -> list[OrderRow]:
        return await self._assemble(self._local_inputs(orders))

    async def _assemble(self, inputs: list[_Input]) -> list[OrderRow]:
        awbs = [i.awb for i in inputs if i.awb]
        rems: dict[str, CodRemittance] = {}
        if awbs:
            found = await self.session.execute(
                select(CodRemittance).where(CodRemittance.awb.in_(awbs))
            )
            rems = {r.awb: r for r in found.scalars().all()}
        rows: list[OrderRow] = []
        for o in inputs:
            row = OrderRow(
                id=o.id,
                order_number=o.order_number,
                order_datetime=o.order_datetime,
                customer_name=o.customer_name,
                customer_phone=o.customer_phone,
                customer_email=o.customer_email,
                amount=o.amount,
                currency=o.currency,
                payment_type=_enum_value(o.payment_type),
                payment_status=_enum_value(o.payment_status),
                fulfillment_status=_enum_value(o.fulfillment_status),
                shipment_status=o.shipment_status,
                courier_name=o.courier_name,
                awb=o.awb,
                tracking_url=TRACKING_URL.format(awb=o.awb) if o.awb else None,
            )
            if o.payment_type == PaymentType.COD:
                rem = rems.get(o.awb) if o.awb else None
                row.expected_cod_amount = o.amount
                row.financial_data = "reported" if rem else "no_data"
                row.bank_verified = False
                if rem:
                    row.collected_amount = rem.collected_amount
                    row.remitted_amount = rem.remitted_amount
                    row.pending_remittance = _pending(rem)
                    row.remittance_status = rem.remittance_status
                    row.settlement_date = rem.settlement_date
                    row.settlement_reference = rem.remittance_reference
                    row.bank_verified = rem.bank_verified_at is not None
                    row.bank_credit_amount = rem.bank_credit_amount
                    row.bank_credit_reference = rem.bank_credit_reference
                    row.bank_credited_on = rem.bank_credited_on
            rows.append(row)
        return rows

    # ----------------------------------------------------------- live mode

    async def _live_inputs(
        self,
        *,
        payment_type: str | None = None,
        fulfillment_status: str | None = None,
        q: str | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
    ) -> tuple[list[_Input], bool, list[str]]:
        """Orders read live from Shopify (bounded) + AWB from `shipment_refs`.
        Live shipment status is not fetched per order (that would be one
        Shiprocket call each); AWB + tracking link only."""
        session = self.session
        reader = get_reader()
        ptype = PaymentType(payment_type) if payment_type else None
        ful = FulfillmentStatus(fulfillment_status) if fulfillment_status else None
        collected = await collect_orders(
            session,
            reader,
            q=q,
            date_from=date_from,
            date_to=date_to,
            payment_type=ptype,
            fulfillment_status=ful,
        )
        orders = list(collected.orders)
        workflows = dict(collected.workflows)

        if q:
            # Shopify cannot search by AWB, so resolve it via our own refs.
            hit_ext = (
                (
                    await session.execute(
                        select(OrderWorkflow.external_order_id)
                        .join(ShipmentRef, ShipmentRef.order_id == OrderWorkflow.id)
                        .where(ShipmentRef.awb.ilike(f"%{q}%"))
                        .limit(200)
                    )
                )
                .scalars()
                .all()
            )
            have = {o["external_id"] for o in orders}
            missing = [e for e in hit_ext if e not in have]
            if missing:
                workflows.update(await get_workflow_by_external_ids(session, missing))
                for o in await reader.get_orders_by_ids(
                    missing, date_from=date_from, date_to=date_to
                ):
                    if ptype and o["payment_type"] != ptype:
                        continue
                    if ful and o["fulfillment_status"] != ful:
                        continue
                    orders.append(o)

        orders = [o for o in orders if not o.get("is_cancelled")]
        ref_ids = {
            o["external_id"]: ref_id_for(o["external_id"], workflows.get(o["external_id"]))
            for o in orders
        }
        latest: dict[uuid.UUID, ShipmentRef] = {}
        if ref_ids:
            refs = (
                (
                    await session.execute(
                        select(ShipmentRef).where(ShipmentRef.order_id.in_(list(ref_ids.values())))
                    )
                )
                .scalars()
                .all()
            )
            for r in sorted(refs, key=lambda r: r.created_at):
                if r.cancelled_at is None:
                    latest[r.order_id] = r  # newest non-cancelled wins
        inputs: list[_Input] = []
        for o in orders:
            rid = ref_ids[o["external_id"]]
            ref = latest.get(rid)
            addr = o.get("shipping_address") or o.get("billing_address") or {}
            inputs.append(
                _Input(
                    id=rid,
                    order_number=o["order_number"],
                    order_datetime=o["order_datetime"],
                    customer_name=addr.get("contact_name"),
                    customer_phone=addr.get("contact_phone"),
                    customer_email=o.get("customer_email"),
                    amount=o["total_amount"],
                    currency=o["currency"],
                    payment_type=o["payment_type"],
                    payment_status=o["payment_status"],
                    fulfillment_status=o["fulfillment_status"],
                    cancelled=False,
                    awb=ref.awb if ref else None,
                    courier_name=None,
                    shipment_status=None,
                )
            )
        inputs.sort(key=lambda i: i.order_datetime, reverse=True)
        return inputs, not collected.incomplete, list(collected.warnings)

    async def _live_summary(
        self, *, date_from: datetime | None, date_to: datetime | None
    ) -> RevenueSummary:
        inputs, complete, warnings = await self._live_inputs(date_from=date_from, date_to=date_to)
        splits: dict[str, dict[str, Bucket]] = {
            t: {k: Bucket() for k in ("total", "fulfilled", "unfulfilled", "partial")}
            for t in ("cod", "prepaid")
        }
        for i in inputs:
            key = _enum_value(i.payment_type)
            if key not in splits:
                continue
            for bucket in (
                splits[key]["total"],
                splits[key].get(_enum_value(i.fulfillment_status)),
            ):
                if bucket is not None:
                    bucket.orders += 1
                    bucket.amount += i.amount
        shipped = [i for i in inputs if i.payment_type == PaymentType.COD and i.awb]
        rem_rows: list[CodRemittance] = []
        if shipped:
            rem_rows = list(
                (
                    await self.session.execute(
                        select(CodRemittance).where(CodRemittance.awb.in_([i.awb for i in shipped]))
                    )
                )
                .scalars()
                .all()
            )
        last_sync = (
            await self.session.execute(select(func.max(CodRemittance.synced_at)))
        ).scalar_one()
        return self._financial_summary(
            splits,
            shipped_count=len(shipped),
            shipped_amount=sum((i.amount for i in shipped), ZERO),
            rem_rows=rem_rows,
            data_source="live",
            complete=complete,
            warnings=warnings,
            last_sync=last_sync,
        )

    @staticmethod
    def _financial_summary(  # noqa: ANN205
        splits,  # noqa: ANN001
        *,
        shipped_count,  # noqa: ANN001
        shipped_amount,  # noqa: ANN001
        rem_rows,  # noqa: ANN001
        data_source,  # noqa: ANN001
        complete,  # noqa: ANN001
        warnings,  # noqa: ANN001
        last_sync,  # noqa: ANN001
    ):
        collected = sum((_dec(r.collected_amount) for r in rem_rows), ZERO)
        remitted = sum((_dec(r.remitted_amount) for r in rem_rows), ZERO)
        pending = sum((_pending(r) or ZERO for r in rem_rows), ZERO)
        verified = sum((_dec(r.bank_credit_amount) for r in rem_rows if r.bank_verified_at), ZERO)
        with_data = len({r.awb for r in rem_rows})
        return RevenueSummary(
            data_source=data_source,
            complete=complete,
            warnings=warnings,
            total_revenue=splits["cod"]["total"].amount + splits["prepaid"]["total"].amount,
            cod=PaymentSplit(**splits["cod"]),
            prepaid=PaymentSplit(**splits["prepaid"]),
            cod_financials=CodFinancials(
                expected_amount=Decimal(shipped_amount),
                collected_amount=collected,
                remitted_amount=remitted,
                pending_remittance=pending,
                bank_verified_amount=verified,
                shipped_orders=shipped_count,
                orders_with_shiprocket_data=with_data,
                orders_without_shiprocket_data=max(shipped_count - with_data, 0),
                remittance_last_synced_at=last_sync,
            ),
        )

    async def list_orders(
        self, *, page: int, page_size: int, **filters: Any
    ) -> tuple[list[OrderRow], int]:
        rows, total, _ = await self.list_orders_with_status(
            page=page, page_size=page_size, **filters
        )
        return rows, total

    async def list_orders_with_status(
        self, *, page: int, page_size: int, **filters: Any
    ) -> tuple[list[OrderRow], int, list[str]]:
        if settings.SHOPIFY_LIVE_MODE:
            inputs, _, warnings = await self._live_inputs(**filters)
            window = inputs[(page - 1) * page_size : page * page_size]
            return await self._assemble(window), len(inputs), warnings
        query = self.orders.search_query(**filters)
        total = (
            await self.session.execute(
                select(func.count()).select_from(query.order_by(None).subquery())
            )
        ).scalar_one()
        stmt = (
            query.order_by(Order.order_datetime.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        orders = list((await self.session.execute(stmt)).scalars().all())
        return await self._rows(orders), total, []

    async def export_xlsx(self, **filters: Any) -> bytes:
        if settings.SHOPIFY_LIVE_MODE:
            inputs, _, _ = await self._live_inputs(**filters)
            rows = await self._assemble(inputs[:MAX_EXPORT_ROWS])
        else:
            orders = await self.orders.list_for_export(
                self.orders.search_query(**filters), limit=MAX_EXPORT_ROWS
            )
            rows = await self._rows(orders)
        wb = Workbook()
        ws = wb.active
        ws.title = "Orders"
        ws.append(
            [
                "Order Number",
                "Order Date",
                "Customer",
                "Phone",
                "Email",
                "Amount",
                "Payment Type",
                "Payment Status",
                "Fulfillment Status",
                "Shipment Status",
                "Courier",
                "AWB",
                "Expected COD",
                "Collected (Shiprocket)",
                "Remitted (Shiprocket)",
                "Pending Remittance",
                "Remittance Status",
                "Settlement Date",
                "Settlement Reference",
                "Bank Verified",
                "Bank Credit Amount",
                "Bank Reference",
                "Bank Credited On",
            ]
        )

        def cell(v: Any) -> Any:
            if isinstance(v, Decimal):
                return float(v)
            if isinstance(v, datetime):
                return v.replace(tzinfo=None)
            return v

        for r in rows:
            verified = None if r.bank_verified is None else ("Yes" if r.bank_verified else "No")
            ws.append(
                [
                    cell(x)
                    for x in (
                        r.order_number,
                        r.order_datetime,
                        r.customer_name,
                        r.customer_phone,
                        r.customer_email,
                        r.amount,
                        r.payment_type,
                        r.payment_status,
                        r.fulfillment_status,
                        r.shipment_status,
                        r.courier_name,
                        r.awb,
                        r.expected_cod_amount,
                        r.collected_amount,
                        r.remitted_amount,
                        r.pending_remittance,
                        r.remittance_status,
                        r.settlement_date,
                        r.settlement_reference,
                        verified,
                        r.bank_credit_amount,
                        r.bank_credit_reference,
                        r.bank_credited_on,
                    )
                ]
            )
        buf = BytesIO()
        wb.save(buf)
        return buf.getvalue()

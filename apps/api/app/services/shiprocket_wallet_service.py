"""Shiprocket wallet: balance snapshots, pre-assignment protection, the
transaction ledger and reporting.

Financial safeguards enforced here:
- Estimated charges live only in `ShiprocketWalletCheck`; they are never
  written to or summed with `ShiprocketWalletTransaction` (actual movements).
- A manual recharge claim is stored `unverified` and is excluded from every
  total until confirmed by evidence (a balance increase observed in
  Shiprocket's own wallet balance, or a matching synced Shiprocket row).
- Sync never overwrites a manually verified row.
- Nothing here initiates a recharge or payment; the module only reads
  Shiprocket (balance, serviceability, optional statement) and writes to the
  OMS tables.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from io import BytesIO
from typing import Any

from openpyxl import Workbook
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import ConflictError, IntegrationError, NotFoundError, ValidationError
from app.core.logging import get_logger
from app.integrations.shiprocket.adapter import ShiprocketAdapter
from app.integrations.shiprocket.wallet import (
    extract_rate_estimate,
    normalize_wallet_transaction,
    parse_wallet_balance,
)
from app.models.shiprocket_wallet import (
    ShiprocketWalletCheck,
    ShiprocketWalletSnapshot,
    ShiprocketWalletTransaction,
)

logger = get_logger(__name__)

ZERO = Decimal("0")
MAX_EXPORT_ROWS = 10_000
SYNC_MAX_PAGES = 100
STALE_AFTER = timedelta(hours=2)

EXPENSE_TYPES = ("freight", "cod_charge", "rto_freight", "weight_discrepancy", "other_debit")
CREDIT_BACK_TYPES = ("freight_reversal", "cod_reversal", "refund", "other_credit")
ALL_TYPES = ("recharge", *EXPENSE_TYPES, *CREDIT_BACK_TYPES)

# Rows that count toward totals: posted by Shiprocket, or a manual claim
# that was confirmed by balance evidence. Unverified claims never count.
_COUNTED = (
    ShiprocketWalletTransaction.status == "confirmed",
    ShiprocketWalletTransaction.source.in_(("shiprocket_api", "balance_confirmed")),
)


def _money(v: float | Decimal) -> Decimal:
    return Decimal(str(v))


class WalletInsufficientError(ConflictError):
    """Raised before AWB assignment when the wallet provably cannot cover it."""

    error_code = "wallet_insufficient"


class AssignContext(BaseModel):
    shipment_ref: str | None = None
    order_number: str | None = None
    shiprocket_order_id: str | None = None
    delivery_pincode: str | None = None
    weight_kg: float = 0.5
    cod: bool = False
    courier_id: str | None = None


class WalletState(BaseModel):
    balance: Decimal | None
    fetched_at: datetime | None
    stale: bool
    low_balance: bool
    low_balance_threshold: Decimal
    guard_enabled: bool
    ledger_sync_enabled: bool
    last_ledger_sync_at: datetime | None


class WalletSummary(BaseModel):
    state: WalletState
    total_recharges: Decimal
    shipping_expenses: Decimal  # freight debits
    cod_charges: Decimal
    rto_expenses: Decimal
    weight_discrepancy_charges: Decimal
    other_debits: Decimal
    credits_and_reversals: Decimal  # reversals + refunds + other credits (not recharges)
    net_shipping_cost: Decimal  # all expense debits minus credits_and_reversals
    pending_count: int
    pending_amount: Decimal
    failed_count: int
    failed_amount: Decimal
    unverified_recharge_claims: int
    opening_balance: Decimal | None
    closing_balance: Decimal | None
    # None when opening/closing are not both known from reported rows.
    ledger_consistent: bool | None
    ledger_gap: Decimal | None


class TxnRow(BaseModel):
    id: uuid.UUID
    transaction_ref: str | None
    txn_date: datetime
    txn_type: str
    direction: str
    amount: Decimal
    balance_after: Decimal | None
    order_number: str | None
    awb: str | None
    courier_name: str | None
    description: str | None
    payment_reference: str | None
    status: str
    source: str
    manually_verified: bool


class WalletService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ------------------------------------------------------------- balance

    async def refresh_balance(
        self, adapter: ShiprocketAdapter, *, trigger: str = "scheduled"
    ) -> ShiprocketWalletSnapshot:
        """Fetches the live balance and stores a timestamped snapshot.
        Raises if the response contains no readable balance -- an unreadable
        response is never recorded as 0."""
        body = await adapter.get_wallet_balance()
        balance = parse_wallet_balance(body)
        if balance is None:
            raise IntegrationError(
                "Shiprocket wallet response did not contain a readable balance. "
                "Run /shiprocket-wallet/probe to inspect its shape.",
                details={"error_type": "validation_error"},
            )
        snap = ShiprocketWalletSnapshot(
            balance=balance,
            fetched_at=datetime.now(UTC),
            trigger=trigger,
            raw_external_payload=body if isinstance(body, dict) else None,
        )
        self.session.add(snap)
        await self.session.commit()
        if balance < _money(settings.SHIPROCKET_WALLET_LOW_BALANCE_THRESHOLD):
            logger.warning(
                "shiprocket_wallet_low_balance",
                balance=str(balance),
                threshold=settings.SHIPROCKET_WALLET_LOW_BALANCE_THRESHOLD,
            )
        return snap

    async def latest_snapshot(self) -> ShiprocketWalletSnapshot | None:
        return (
            await self.session.execute(
                select(ShiprocketWalletSnapshot)
                .order_by(ShiprocketWalletSnapshot.fetched_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

    async def state(self) -> WalletState:
        snap = await self.latest_snapshot()
        threshold = _money(settings.SHIPROCKET_WALLET_LOW_BALANCE_THRESHOLD)
        last_sync = (
            await self.session.execute(
                select(func.max(ShiprocketWalletTransaction.synced_at)).where(
                    ShiprocketWalletTransaction.source == "shiprocket_api"
                )
            )
        ).scalar_one()
        fetched = snap.fetched_at if snap else None
        if fetched is not None and fetched.tzinfo is None:
            fetched = fetched.replace(tzinfo=UTC)
        return WalletState(
            balance=snap.balance if snap else None,
            fetched_at=fetched,
            stale=fetched is None or datetime.now(UTC) - fetched > STALE_AFTER,
            low_balance=bool(snap and snap.balance < threshold),
            low_balance_threshold=threshold,
            guard_enabled=settings.SHIPROCKET_WALLET_GUARD_ENABLED,
            ledger_sync_enabled=bool(settings.SHIPROCKET_WALLET_STATEMENT_PATH),
            last_ledger_sync_at=last_sync,
        )

    # --------------------------------------------------- pre-assignment guard

    async def _estimate(
        self, adapter: ShiprocketAdapter, ctx: AssignContext
    ) -> tuple[Decimal, str | None] | None:
        attempts: list[dict[str, Any]] = []
        if ctx.shiprocket_order_id:
            attempts.append({"order_id": ctx.shiprocket_order_id})
        pickup = getattr(settings, "SHIPROCKET_PICKUP_PINCODE", None)
        if pickup and ctx.delivery_pincode:
            attempts.append(
                {
                    "pickup_postcode": pickup,
                    "delivery_postcode": ctx.delivery_pincode,
                    "weight": ctx.weight_kg,
                    "cod": 1 if ctx.cod else 0,
                }
            )
        for params in attempts:
            try:
                body = await adapter.get_serviceability(params)
            except IntegrationError as exc:
                logger.warning("wallet_guard_estimate_failed", error=exc.message)
                continue
            found = extract_rate_estimate(body, courier_id=ctx.courier_id)
            if found is not None:
                return found
        return None

    async def check_before_assignment(self, adapter: ShiprocketAdapter, ctx: AssignContext) -> None:
        """Raises `WalletInsufficientError` only when a freshly fetched numeric
        balance is below (estimated charge + buffer). Any failure to read the
        balance/estimate lets the shipment proceed, recorded as
        `allowed_unverified`. Reads Shiprocket only; creates/assigns nothing,
        so a failed check can never leave a half-created shipment behind."""
        if not settings.SHIPROCKET_WALLET_GUARD_ENABLED:
            return
        buffer = _money(settings.SHIPROCKET_WALLET_SAFETY_BUFFER)
        balance: Decimal | None = None
        detail: str | None = None
        try:
            balance = (await self.refresh_balance(adapter, trigger="pre_assignment")).balance
        except IntegrationError as exc:
            detail = f"Balance unavailable: {exc.message}"
            logger.warning("wallet_guard_balance_unavailable", error=exc.message)

        estimate = await self._estimate(adapter, ctx)
        estimated = estimate[0] if estimate else None
        fallback = _money(settings.SHIPROCKET_WALLET_FALLBACK_CHARGE)
        required = (estimated if estimated is not None else fallback) + buffer
        if estimated is None and detail is None:
            detail = "Charge estimate unavailable; checked against buffer/fallback only."

        outcome = "allowed"
        shortfall: Decimal | None = None
        if balance is None:
            outcome = "allowed_unverified"
        elif balance < required:
            outcome = "blocked"
            shortfall = required - balance

        self.session.add(
            ShiprocketWalletCheck(
                shipment_ref=ctx.shipment_ref,
                order_number=ctx.order_number,
                balance=balance,
                estimated_charge=estimated,
                safety_buffer=buffer,
                required_minimum=required if balance is not None else None,
                shortfall=shortfall,
                outcome=outcome,
                detail=detail,
                checked_at=datetime.now(UTC),
            )
        )
        await self.session.commit()
        if outcome != "blocked":
            return
        assert balance is not None and shortfall is not None
        raise WalletInsufficientError(
            f"Shiprocket wallet balance is too low to assign a courier. "
            f"Current balance: ₹{balance:.2f}. "
            f"Estimated shipping charge: "
            f"{'₹' + format(estimated, '.2f') if estimated is not None else 'unavailable'}. "
            f"Required minimum balance (incl. ₹{buffer:.2f} buffer): ₹{required:.2f}. "
            f"Recharge needed: ₹{shortfall:.2f}. "
            "Recharge in the Shiprocket dashboard, then retry; no shipment was changed.",
            details={
                "current_balance": str(balance),
                "estimated_charge": str(estimated) if estimated is not None else None,
                "safety_buffer": str(buffer),
                "required_minimum": str(required),
                "recharge_needed": str(shortfall),
            },
        )

    # ------------------------------------------------------------ ledger sync

    async def sync_transactions(self, adapter: ShiprocketAdapter) -> dict[str, int]:
        now = datetime.now(UTC)
        created = updated = skipped = unparsed = seen = 0
        page = 1
        while page <= SYNC_MAX_PAGES:
            records, has_more = await adapter.fetch_wallet_statement(page=page)
            seen += len(records)
            for raw in records:
                values = normalize_wallet_transaction(raw)
                if values is None:
                    unparsed += 1
                    continue
                existing = (
                    await self.session.execute(
                        select(ShiprocketWalletTransaction).where(
                            ShiprocketWalletTransaction.dedup_key == values["dedup_key"]
                        )
                    )
                ).scalar_one_or_none()
                if existing is None:
                    self.session.add(
                        ShiprocketWalletTransaction(
                            **values,
                            source="shiprocket_api",
                            synced_at=now,
                            raw_external_payload=raw,
                        )
                    )
                    created += 1
                    if values["txn_type"] == "recharge":
                        await self._supersede_claim(values["amount"], values["txn_date"])
                elif existing.manually_verified:
                    skipped += 1  # never overwrite a manually verified record
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
        if seen and created + updated + skipped == 0:
            raise ValidationError(
                f"Shiprocket returned {seen} statement rows but none had a readable date, "
                "amount and credit/debit direction. Run /shiprocket-wallet/probe and adjust "
                "the parser; nothing was stored."
            )
        logger.info(
            "shiprocket_wallet_sync_done",
            created=created,
            updated=updated,
            skipped=skipped,
            unparsed=unparsed,
        )
        return {"created": created, "updated": updated, "skipped": skipped, "unparsed": unparsed}

    async def _supersede_claim(self, amount: Decimal, when: datetime) -> None:
        """A real Shiprocket recharge row replaces a matching manual claim so
        the same money is never counted twice."""
        claims = (
            (
                await self.session.execute(
                    select(ShiprocketWalletTransaction).where(
                        ShiprocketWalletTransaction.source.in_(
                            ("manual_claim", "balance_confirmed")
                        ),
                        ShiprocketWalletTransaction.txn_type == "recharge",
                        ShiprocketWalletTransaction.amount == amount,
                        ShiprocketWalletTransaction.status.in_(("unverified", "confirmed")),
                    )
                )
            )
            .scalars()
            .all()
        )
        for claim in claims:
            claim.status = "superseded"
            claim.verification_note = (
                (claim.verification_note or "") + " Superseded by a Shiprocket-reported recharge."
            ).strip()

    # ------------------------------------------------------------- recharges

    async def create_recharge_claim(
        self,
        *,
        amount: Decimal,
        payment_reference: str,
        note: str | None,
        user_id: uuid.UUID | None,
    ) -> ShiprocketWalletTransaction:
        """Records that someone says they recharged. `unverified` and excluded
        from all totals until `confirm_claim_by_balance` finds evidence."""
        if amount <= 0 or not payment_reference.strip():
            raise ValidationError(
                "A recharge claim needs a positive amount and a payment reference."
            )
        claim_id = uuid.uuid4()
        row = ShiprocketWalletTransaction(
            dedup_key=f"claim:{claim_id}",
            txn_date=datetime.now(UTC),
            txn_type="recharge",
            direction="credit",
            amount=amount,
            payment_reference=payment_reference.strip()[:128],
            description=note,
            status="unverified",
            source="manual_claim",
            created_by=user_id,
        )
        self.session.add(row)
        await self.session.commit()
        return row

    async def confirm_claim_by_balance(
        self, adapter: ShiprocketAdapter, claim_id: uuid.UUID, *, user_id: uuid.UUID | None
    ) -> ShiprocketWalletTransaction:
        """Confirms a claim only if Shiprocket's own wallet balance rose by at
        least the claimed amount since just before the claim was made."""
        claim = await self.session.get(ShiprocketWalletTransaction, claim_id)
        if claim is None or claim.source != "manual_claim":
            raise NotFoundError("Recharge claim not found.")
        if claim.status != "unverified":
            raise ConflictError(f"This claim is already {claim.status}.")
        claimed_at = claim.txn_date  # Python-set time, not DB-clock rounded
        if claimed_at.tzinfo is None:
            claimed_at = claimed_at.replace(tzinfo=UTC)
        before = (
            await self.session.execute(
                select(ShiprocketWalletSnapshot)
                .where(ShiprocketWalletSnapshot.fetched_at <= claimed_at)
                .order_by(ShiprocketWalletSnapshot.fetched_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if before is None:
            raise ValidationError(
                "No wallet balance was recorded before this claim, so it cannot be verified."
            )
        after = await self.refresh_balance(adapter, trigger="manual")
        if after.balance - before.balance < claim.amount:
            raise ValidationError(
                f"Shiprocket's wallet balance went from ₹{before.balance:.2f} to "
                f"₹{after.balance:.2f}; that does not show a ₹{claim.amount:.2f} recharge. "
                "Claim left unverified."
            )
        claim.status = "confirmed"
        claim.source = "balance_confirmed"
        claim.manually_verified = True
        claim.verified_by = user_id
        claim.verified_at = datetime.now(UTC)
        claim.verification_note = (
            f"Wallet balance rose from {before.balance} to {after.balance} after the claim."
        )
        await self.session.commit()
        return claim

    # --------------------------------------------------------------- reporting

    @staticmethod
    def _range(stmt, date_from, date_to):  # noqa: ANN001, ANN205
        if date_from:
            stmt = stmt.where(ShiprocketWalletTransaction.txn_date >= date_from)
        if date_to:
            stmt = stmt.where(ShiprocketWalletTransaction.txn_date <= date_to)
        return stmt

    async def summary(
        self, *, date_from: datetime | None, date_to: datetime | None
    ) -> WalletSummary:
        T = ShiprocketWalletTransaction
        grouped = self._range(
            select(T.txn_type, func.coalesce(func.sum(T.amount), 0))
            .where(*_COUNTED)
            .group_by(T.txn_type),
            date_from,
            date_to,
        )
        by_type: dict[str, Decimal] = {
            t: Decimal(str(a)) for t, a in (await self.session.execute(grouped)).all()
        }

        def amt(*types: str) -> Decimal:
            return sum((by_type.get(t, ZERO) for t in types), ZERO)

        async def status_totals(status: str) -> tuple[int, Decimal]:
            row = (
                await self.session.execute(
                    self._range(
                        select(func.count(T.id), func.coalesce(func.sum(T.amount), 0)).where(
                            T.status == status, T.source == "shiprocket_api"
                        ),
                        date_from,
                        date_to,
                    )
                )
            ).one()
            return row[0], Decimal(str(row[1]))

        pending_n, pending_amt = await status_totals("pending")
        failed_n, failed_amt = await status_totals("failed")
        claims = (
            await self.session.execute(
                select(func.count(T.id)).where(T.status == "unverified", T.source == "manual_claim")
            )
        ).scalar_one()

        opening = closing = None
        if date_from:
            opening = (
                await self.session.execute(
                    select(T.balance_after)
                    .where(*_COUNTED, T.txn_date < date_from, T.balance_after.is_not(None))
                    .order_by(T.txn_date.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
        closing_q = select(T.balance_after).where(*_COUNTED, T.balance_after.is_not(None))
        if date_to:
            closing_q = closing_q.where(T.txn_date <= date_to)
        closing = (
            await self.session.execute(closing_q.order_by(T.txn_date.desc()).limit(1))
        ).scalar_one_or_none()
        credits = amt("recharge", *CREDIT_BACK_TYPES)
        debits = amt(*EXPENSE_TYPES)
        consistent = gap = None
        if opening is not None and closing is not None:
            gap = (closing - opening) - (credits - debits)
            consistent = gap == 0

        expense_total = amt(*EXPENSE_TYPES)
        back = amt(*CREDIT_BACK_TYPES)
        return WalletSummary(
            state=await self.state(),
            total_recharges=amt("recharge"),
            shipping_expenses=amt("freight"),
            cod_charges=amt("cod_charge"),
            rto_expenses=amt("rto_freight"),
            weight_discrepancy_charges=amt("weight_discrepancy"),
            other_debits=amt("other_debit"),
            credits_and_reversals=back,
            net_shipping_cost=expense_total - back,
            pending_count=pending_n,
            pending_amount=pending_amt,
            failed_count=failed_n,
            failed_amount=failed_amt,
            unverified_recharge_claims=claims,
            opening_balance=opening,
            closing_balance=closing,
            ledger_consistent=consistent,
            ledger_gap=gap,
        )

    def _filtered(self, *, q, date_from, date_to, txn_type, direction, courier, status):  # noqa: ANN001, ANN202
        T = ShiprocketWalletTransaction
        stmt = select(T)
        if q:
            like = f"%{q}%"
            stmt = stmt.where(
                or_(T.order_number.ilike(like), T.awb.ilike(like), T.transaction_ref.ilike(like))
            )
        if txn_type:
            stmt = stmt.where(T.txn_type == txn_type)
        if direction:
            stmt = stmt.where(T.direction == direction)
        if courier:
            stmt = stmt.where(T.courier_name.ilike(f"%{courier}%"))
        if status:
            stmt = stmt.where(T.status == status)
        return self._range(stmt, date_from, date_to)

    async def list_transactions(
        self, *, page: int, page_size: int, **filters: Any
    ) -> tuple[list[TxnRow], int]:
        T = ShiprocketWalletTransaction
        query = self._filtered(**filters)
        total = (
            await self.session.execute(select(func.count()).select_from(query.subquery()))
        ).scalar_one()
        rows = (
            (
                await self.session.execute(
                    query.order_by(T.txn_date.desc())
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            )
            .scalars()
            .all()
        )
        return [self._row(r) for r in rows], total

    @staticmethod
    def _row(r: ShiprocketWalletTransaction) -> TxnRow:
        return TxnRow(
            id=r.id,
            transaction_ref=r.transaction_ref,
            txn_date=r.txn_date,
            txn_type=r.txn_type,
            direction=r.direction,
            amount=r.amount,
            balance_after=r.balance_after,
            order_number=r.order_number,
            awb=r.awb,
            courier_name=r.courier_name,
            description=r.description,
            payment_reference=r.payment_reference,
            status=r.status,
            source=r.source,
            manually_verified=r.manually_verified,
        )

    async def monthly_summary(
        self, *, date_from: datetime | None, date_to: datetime | None, by: str = "month"
    ) -> list[dict[str, Any]]:
        T = ShiprocketWalletTransaction
        rows = (
            (
                await self.session.execute(
                    self._range(select(T).where(*_COUNTED), date_from, date_to).limit(50_000)
                )
            )
            .scalars()
            .all()
        )
        buckets: dict[str, dict[str, Decimal]] = defaultdict(lambda: defaultdict(lambda: ZERO))
        for r in rows:
            key = r.txn_date.strftime("%Y-%m" if by == "month" else "%Y")
            buckets[key][r.txn_type] += r.amount
        out = []
        for key in sorted(buckets):
            b = buckets[key]
            expenses = sum((b[t] for t in EXPENSE_TYPES), ZERO)
            back = sum((b[t] for t in CREDIT_BACK_TYPES), ZERO)
            out.append(
                {
                    "period": key,
                    "recharges": b["recharge"],
                    "shipping_expenses": b["freight"],
                    "cod_charges": b["cod_charge"],
                    "rto_expenses": b["rto_freight"],
                    "weight_discrepancy": b["weight_discrepancy"],
                    "credits_and_reversals": back,
                    "net_shipping_cost": expenses - back,
                }
            )
        return out

    async def order_expenses(self, *, order_number: str | None, awb: str | None) -> dict[str, Any]:
        """Actual wallet movements for an order/AWB, and -- separately -- the
        pre-assignment estimates. Estimates are never added to actuals."""
        if not order_number and not awb:
            raise ValidationError("Provide an order number or AWB.")
        T = ShiprocketWalletTransaction
        cond = []
        if order_number:
            cond.append(T.order_number == order_number)
        if awb:
            cond.append(T.awb == awb)
        txns = (
            (await self.session.execute(select(T).where(or_(*cond)).order_by(T.txn_date)))
            .scalars()
            .all()
        )
        counted = [t for t in txns if t.status == "confirmed" and t.source != "manual_claim"]
        actual_debits = sum((t.amount for t in counted if t.direction == "debit"), ZERO)
        actual_credits = sum((t.amount for t in counted if t.direction == "credit"), ZERO)
        checks = []
        if order_number:
            checks = (
                (
                    await self.session.execute(
                        select(ShiprocketWalletCheck)
                        .where(ShiprocketWalletCheck.order_number == order_number)
                        .order_by(ShiprocketWalletCheck.checked_at)
                    )
                )
                .scalars()
                .all()
            )
        return {
            "actual": {
                "transactions": [self._row(t).model_dump(mode="json") for t in txns],
                "total_debits": actual_debits,
                "total_credits": actual_credits,
                "net_cost": actual_debits - actual_credits,
            },
            "estimated": [
                {
                    "checked_at": c.checked_at,
                    "estimated_charge": c.estimated_charge,
                    "balance": c.balance,
                    "outcome": c.outcome,
                }
                for c in checks
            ],
            "note": "Estimated charges are pre-assignment estimates, not wallet debits.",
        }

    async def export_xlsx(self, **filters: Any) -> bytes:
        T = ShiprocketWalletTransaction
        rows = (
            (
                await self.session.execute(
                    self._filtered(**filters).order_by(T.txn_date.desc()).limit(MAX_EXPORT_ROWS)
                )
            )
            .scalars()
            .all()
        )
        wb = Workbook()
        ws = wb.active
        ws.title = "Wallet transactions"
        ws.append(
            [
                "Date",
                "Reference",
                "Type",
                "Direction",
                "Amount",
                "Balance After",
                "Order",
                "AWB",
                "Courier",
                "Description",
                "Payment Reference",
                "Status",
                "Source",
                "Manually Verified",
            ]
        )
        for r in rows:
            ws.append(
                [
                    r.txn_date.replace(tzinfo=None),
                    r.transaction_ref,
                    r.txn_type,
                    r.direction,
                    float(r.amount),
                    float(r.balance_after) if r.balance_after is not None else None,
                    r.order_number,
                    r.awb,
                    r.courier_name,
                    r.description,
                    r.payment_reference,
                    r.status,
                    r.source,
                    "Yes" if r.manually_verified else "No",
                ]
            )
        buf = BytesIO()
        wb.save(buf)
        return buf.getvalue()

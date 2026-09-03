from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models.cashfree_settlement import CashfreeSettlement
from app.repositories.base import BaseRepository


class CashfreeSettlementRepository(BaseRepository[CashfreeSettlement]):
    model = CashfreeSettlement

    async def get_by_cf_settlement_id(self, cf_settlement_id: str) -> CashfreeSettlement | None:
        stmt = select(CashfreeSettlement).where(
            CashfreeSettlement.cf_settlement_id == cf_settlement_id
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def upsert_by_cf_settlement_id(
        self, *, cf_settlement_id: str, **fields: Any
    ) -> tuple[CashfreeSettlement, bool]:
        """Idempotent create-or-update keyed by `cf_settlement_id` —
        unlike `Payment`/`PaymentTransaction` (append-only events, a
        duplicate delivery is a no-op), a settlement's own `status`
        genuinely progresses over time (PENDING -> SUCCESS), so
        re-syncing the same settlement must UPDATE it in place, not skip
        it — matches `BaseRepository.upsert_by_external_id`'s existing
        create-or-update shape, just keyed on this table's own natural
        id instead of `(source_system, external_id)`.
        """
        existing = await self.get_by_cf_settlement_id(cf_settlement_id)
        if existing is not None:
            return await self.update(existing, **fields), False

        try:
            instance = await self.create(cf_settlement_id=cf_settlement_id, **fields)
            return instance, True
        except IntegrityError:
            await self.session.rollback()
            existing = await self.get_by_cf_settlement_id(cf_settlement_id)
            if existing is None:
                raise
            return await self.update(existing, **fields), False

    async def get_last_settled(self) -> CashfreeSettlement | None:
        """Most recent settlement Cashfree has actually completed
        (`status="SUCCESS"`), ordered by when it was processed — backs
        the Payments UI's "Last Settled" figure.
        """
        stmt = (
            select(CashfreeSettlement)
            .where(CashfreeSettlement.status == "SUCCESS")
            .order_by(CashfreeSettlement.settlement_processed_on.desc())
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_unsettled_total(self) -> Decimal:
        """Sum of `amount_settled` (falling back to `payment_amount` when
        a still-pending settlement has no `amount_settled` yet — Cashfree
        only finalizes that figure once a settlement actually completes)
        across every settlement not yet in a terminal `SUCCESS`/`FAILED`
        state. A DERIVED figure — see `CashfreeSyncService`'s module
        docstring for why this isn't a Cashfree-native field.
        """
        stmt = select(CashfreeSettlement).where(
            CashfreeSettlement.status.in_(["PENDING", "PENDING_WITH_CASHFREE", "PENDING_WITH_BANK"])
        )
        result = await self.session.execute(stmt)
        rows = result.scalars().all()
        total = Decimal("0")
        for row in rows:
            total += row.amount_settled or row.payment_amount or Decimal("0")
        return total

    async def get_nearest_upcoming(self) -> CashfreeSettlement | None:
        """Nearest not-yet-completed settlement, earliest-initiated
        first — the DERIVED stand-in for "Upcoming Settlement" (see
        `CashfreeSyncService`'s module docstring: no confirmed Cashfree-
        native endpoint for this exact figure was available).
        """
        stmt = (
            select(CashfreeSettlement)
            .where(
                CashfreeSettlement.status.in_(
                    ["PENDING", "PENDING_WITH_CASHFREE", "PENDING_WITH_BANK"]
                )
            )
            .order_by(CashfreeSettlement.settlement_initiated_on.asc())
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_recent(self, *, limit: int = 20) -> list[CashfreeSettlement]:
        stmt = (
            select(CashfreeSettlement)
            .order_by(CashfreeSettlement.settlement_processed_on.desc().nulls_last())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import and_, exists, func, or_, select

from app.core.timezone import to_ist
from app.models.order import Order
from app.models.shipment import Shipment, ShipmentEvent
from app.repositories.base import AppendOnlyRepository, BaseRepository


def _confirmed_by_scope(telecaller_ids: list[uuid.UUID] | None):
    """`EXISTS`-based scope filter shared by every dashboard aggregate
    below — "this Shipment's Order was confirmed by one of these
    Telecallers." `None` means unscoped (Admin/Fulfillment, unchanged
    behavior); an empty list correctly matches nothing (a Shipment Staff
    user with no Telecallers assigned yet sees zero rows, never
    everything) since `Order.confirmed_by_telecaller_id.in_([])` is
    always false.
    """
    if telecaller_ids is None:
        return None
    return exists(
        select(1).where(
            and_(
                Order.id == Shipment.order_id,
                Order.confirmed_by_telecaller_id.in_(telecaller_ids),
            )
        )
    )


class ShipmentRepository(BaseRepository[Shipment]):
    model = Shipment

    async def status_counts(
        self, *, telecaller_ids: list[uuid.UUID] | None = None
    ) -> dict[str, int]:
        """Whole-table breakdown by `current_status` — backs the shipment
        dashboard's summary cards and the analytics status breakdown.
        `telecaller_ids` scopes it to a Shipment Staff user's permitted
        Telecallers (see `_confirmed_by_scope`); omitted entirely for
        Admin/Fulfillment, preserving the exact prior unscoped query.
        """
        stmt = select(Shipment.current_status, func.count())
        scope = _confirmed_by_scope(telecaller_ids)
        if scope is not None:
            stmt = stmt.where(scope)
        stmt = stmt.group_by(Shipment.current_status)
        rows = (await self.session.execute(stmt)).all()
        return {status.value: count for status, count in rows}

    async def payment_type_counts(
        self, *, telecaller_ids: list[uuid.UUID] | None = None
    ) -> dict[str, int]:
        stmt = (
            select(Order.payment_type, func.count())
            .select_from(Shipment)
            .join(Order, Order.id == Shipment.order_id)
        )
        if telecaller_ids is not None:
            stmt = stmt.where(Order.confirmed_by_telecaller_id.in_(telecaller_ids))
        stmt = stmt.group_by(Order.payment_type)
        rows = (await self.session.execute(stmt)).all()
        return {ptype.value: count for ptype, count in rows}

    async def count_created_in_range(
        self,
        *,
        date_from: datetime,
        date_to: datetime,
        telecaller_ids: list[uuid.UUID] | None = None,
    ) -> int:
        stmt = select(func.count()).where(
            Shipment.created_at >= date_from, Shipment.created_at <= date_to
        )
        scope = _confirmed_by_scope(telecaller_ids)
        if scope is not None:
            stmt = stmt.where(scope)
        return int(await self.session.scalar(stmt) or 0)

    async def daily_created_and_delivered(
        self,
        *,
        date_from: datetime,
        date_to: datetime,
        telecaller_ids: list[uuid.UUID] | None = None,
    ) -> list[dict[str, object]]:
        """Day-bucketed (IST) shipment creation + delivery counts — both
        from real, already-existing timestamps (`Shipment.created_at`,
        `Shipment.actual_delivery_date`), never a fabricated "shipped at"
        column. A per-order fetch-then-bucket-in-Python, same portability
        rationale as `CallAttemptRepository.resolve_current_for_order`
        (small result set for a dashboard trend, dialect-portable without
        a Postgres-only `date_trunc`).
        """
        created_stmt = select(Shipment.created_at).where(
            Shipment.created_at >= date_from, Shipment.created_at <= date_to
        )
        delivered_stmt = select(Shipment.actual_delivery_date).where(
            Shipment.actual_delivery_date >= date_from,
            Shipment.actual_delivery_date <= date_to,
        )
        scope = _confirmed_by_scope(telecaller_ids)
        if scope is not None:
            created_stmt = created_stmt.where(scope)
            delivered_stmt = delivered_stmt.where(scope)
        created_rows = (await self.session.execute(created_stmt)).scalars().all()
        delivered_rows = (await self.session.execute(delivered_stmt)).scalars().all()

        buckets: dict[str, dict[str, int]] = {}
        for ts in created_rows:
            key = to_ist(ts).date().isoformat()
            buckets.setdefault(key, {"created": 0, "delivered": 0})["created"] += 1
        for delivered_ts in delivered_rows:
            if delivered_ts is None:
                continue
            key = to_ist(delivered_ts).date().isoformat()
            buckets.setdefault(key, {"created": 0, "delivered": 0})["delivered"] += 1
        return [{"date": date, **counts} for date, counts in sorted(buckets.items())]

    async def get_by_awb(self, awb: str) -> Shipment | None:
        stmt = select(Shipment).where(Shipment.awb == awb)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_for_order(self, order_id: uuid.UUID) -> list[Shipment]:
        stmt = select(Shipment).where(Shipment.order_id == order_id)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    def search_query(
        self,
        *,
        q: str | None = None,
        status: str | None = None,
        courier_id: uuid.UUID | None = None,
        order_id: uuid.UUID | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        telecaller_ids: list[uuid.UUID] | None = None,
    ):
        stmt = self._base_query()
        if q:
            stmt = stmt.where(
                or_(Shipment.awb.ilike(f"%{q}%"), Shipment.shiprocket_shipment_id.ilike(f"%{q}%"))
            )
        if status:
            stmt = stmt.where(Shipment.current_status == status)
        if courier_id:
            stmt = stmt.where(Shipment.courier_id == courier_id)
        if order_id:
            stmt = stmt.where(Shipment.order_id == order_id)
        if date_from:
            stmt = stmt.where(Shipment.expected_delivery_date >= date_from)
        if date_to:
            stmt = stmt.where(Shipment.expected_delivery_date <= date_to)
        scope = _confirmed_by_scope(telecaller_ids)
        if scope is not None:
            stmt = stmt.where(scope)
        return stmt


class ShipmentEventRepository(AppendOnlyRepository[ShipmentEvent]):
    model = ShipmentEvent

    async def list_for_shipment(self, shipment_id: uuid.UUID) -> list[ShipmentEvent]:
        stmt = (
            select(ShipmentEvent)
            .where(ShipmentEvent.shipment_id == shipment_id)
            .order_by(ShipmentEvent.event_timestamp.asc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def find_duplicate(
        self,
        *,
        shipment_id: uuid.UUID,
        external_event_id: str | None,
        status: str,
        event_timestamp: datetime,
    ) -> ShipmentEvent | None:
        if external_event_id:
            stmt = select(ShipmentEvent).where(
                ShipmentEvent.shipment_id == shipment_id,
                ShipmentEvent.external_event_id == external_event_id,
            )
        else:
            stmt = select(ShipmentEvent).where(
                ShipmentEvent.shipment_id == shipment_id,
                ShipmentEvent.status == status,
                ShipmentEvent.event_timestamp == event_timestamp,
            )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

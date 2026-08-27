from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Select, and_, func, or_, select
from sqlalchemy.orm import aliased, selectinload

from app.models.enums import AssignmentStatus
from app.models.order import Order
from app.models.telecalling import CallAttempt, OrderAssignment
from app.repositories.base import AppendOnlyRepository, BaseRepository
from app.schemas.common import PageParams


class OrderAssignmentRepository(BaseRepository[OrderAssignment]):
    model = OrderAssignment

    async def get_active_for_order(self, order_id: uuid.UUID) -> OrderAssignment | None:
        stmt = select(OrderAssignment).where(
            OrderAssignment.order_id == order_id,
            OrderAssignment.assignment_status == AssignmentStatus.ACTIVE,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_unfulfilled_pool(
        self,
        *,
        team_leader_id: uuid.UUID | None,
        call_status: str | None,
        date_from: datetime | None,
        date_to: datetime | None,
        page_params: PageParams,
    ) -> tuple[list[tuple[Order, OrderAssignment | None]], int]:
        """The Team Leader's "Unfulfilled Orders" browsing surface — every
        unfulfilled order that's either unassigned (available to grab) or
        already assigned within the caller's own team, so there's
        something to select from in the first place. Deliberately a LEFT
        OUTER JOIN (unlike `search_query`'s INNER JOIN, used everywhere
        else an order is *already* known to be assigned) — the join
        condition, not a WHERE clause, carries the ACTIVE filter, so an
        order with zero assignment rows still surfaces with a NULL
        assignment side instead of being dropped.
        """
        from app.models.enums import FulfillmentStatus

        active_assignment = aliased(OrderAssignment)
        stmt = (
            select(Order, active_assignment)
            .outerjoin(
                active_assignment,
                and_(
                    active_assignment.order_id == Order.id,
                    active_assignment.assignment_status == AssignmentStatus.ACTIVE,
                ),
            )
            .where(Order.fulfillment_status == FulfillmentStatus.UNFULFILLED)
            .options(selectinload(Order.customer), selectinload(Order.items))
        )
        if team_leader_id is not None:
            # Admin (team_leader_id=None) sees the whole pool; a Team
            # Leader sees unassigned orders plus whatever's already on
            # their own team — never another team's in-progress work.
            stmt = stmt.where(
                or_(
                    active_assignment.id.is_(None),
                    active_assignment.team_leader_id == team_leader_id,
                )
            )
        if call_status:
            stmt = stmt.where(active_assignment.current_status == call_status)
        if date_from is not None:
            stmt = stmt.where(Order.order_datetime >= date_from)
        if date_to is not None:
            stmt = stmt.where(Order.order_datetime <= date_to)

        total = await self.session.scalar(select(func.count()).select_from(stmt.subquery()))
        stmt = (
            stmt.order_by(Order.order_datetime.desc())
            .offset(page_params.offset)
            .limit(page_params.page_size)
        )
        rows = (await self.session.execute(stmt)).all()
        return [(row[0], row[1]) for row in rows], total or 0

    async def list_for_order(self, order_id: uuid.UUID) -> list[OrderAssignment]:
        """Full assignment history for one order, oldest first — never
        filtered to ACTIVE only, since history must stay reconstructable.
        """
        stmt = (
            select(OrderAssignment)
            .where(OrderAssignment.order_id == order_id)
            .order_by(OrderAssignment.assigned_at.asc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    def _base_scope_query(self) -> Select:
        return (
            select(OrderAssignment)
            .join(Order, Order.id == OrderAssignment.order_id)
            .where(OrderAssignment.assignment_status == AssignmentStatus.ACTIVE)
            .options(
                selectinload(OrderAssignment.order).selectinload(Order.customer),
                selectinload(OrderAssignment.order).selectinload(Order.items),
                selectinload(OrderAssignment.order).selectinload(Order.shipments),
            )
        )

    def search_query(
        self,
        *,
        assigned_to: uuid.UUID | None = None,
        team_leader_id: uuid.UUID | None = None,
        unfulfilled_only: bool = False,
        call_status: str | None = None,
        follow_up_from: datetime | None = None,
        follow_up_to: datetime | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
    ) -> Select:
        """The one place row-scoping is applied. `assigned_to`/
        `team_leader_id` must always be derived from the *authenticated*
        current_user server-side — never from a client-supplied query
        param — this is the actual security boundary described in the
        RBAC spec: a Telecaller's `/telecaller/orders` request always
        passes `assigned_to=current_user.id`, never a value read off the
        request.
        """
        stmt = self._base_scope_query()
        if assigned_to is not None:
            stmt = stmt.where(OrderAssignment.assigned_to == assigned_to)
        if team_leader_id is not None:
            stmt = stmt.where(OrderAssignment.team_leader_id == team_leader_id)
        if unfulfilled_only:
            from app.models.enums import FulfillmentStatus

            stmt = stmt.where(Order.fulfillment_status == FulfillmentStatus.UNFULFILLED)
        if call_status:
            stmt = stmt.where(OrderAssignment.current_status == call_status)
        if follow_up_from is not None:
            stmt = stmt.where(OrderAssignment.next_follow_up_at >= follow_up_from)
        if follow_up_to is not None:
            stmt = stmt.where(OrderAssignment.next_follow_up_at < follow_up_to)
        if date_from is not None:
            stmt = stmt.where(Order.order_datetime >= date_from)
        if date_to is not None:
            stmt = stmt.where(Order.order_datetime <= date_to)
        return stmt

    async def get_scoped(
        self,
        order_id: uuid.UUID,
        *,
        assigned_to: uuid.UUID | None,
        team_leader_id: uuid.UUID | None,
    ) -> OrderAssignment | None:
        """Single-order lookup pre-scoped the same way `search_query` is —
        used by every `/telecaller/orders/{id}` and `/team/orders/{id}`
        detail read. Returns `None` (never leaks the row) if it exists but
        is outside the caller's scope.
        """
        stmt = self._base_scope_query().where(OrderAssignment.order_id == order_id)
        if assigned_to is not None:
            stmt = stmt.where(OrderAssignment.assigned_to == assigned_to)
        if team_leader_id is not None:
            stmt = stmt.where(OrderAssignment.team_leader_id == team_leader_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def team_summary_counts(self, *, team_leader_id: uuid.UUID | None) -> dict[str, int]:
        """One aggregate query, not a Python loop over rows — required by
        the 10,000+ order performance constraint.
        """
        stmt = select(OrderAssignment.current_status, func.count()).where(
            OrderAssignment.assignment_status == AssignmentStatus.ACTIVE
        )
        if team_leader_id is not None:
            stmt = stmt.where(OrderAssignment.team_leader_id == team_leader_id)
        stmt = stmt.group_by(OrderAssignment.current_status)
        rows = (await self.session.execute(stmt)).all()
        return {status.value: count for status, count in rows}

    async def telecaller_performance(
        self, *, team_leader_id: uuid.UUID | None, telecaller_id: uuid.UUID | None = None
    ) -> list[tuple[uuid.UUID, dict[str, int]]]:
        """Per-telecaller status-count breakdown (plus a `"_follow_ups"`
        pseudo-status counting assignments with any pending follow-up
        date) in two grouped queries — backs both `GET /team/telecallers`
        (whole team) and a single telecaller's own summary.
        """
        base_filters = [OrderAssignment.assignment_status == AssignmentStatus.ACTIVE]
        if team_leader_id is not None:
            base_filters.append(OrderAssignment.team_leader_id == team_leader_id)
        if telecaller_id is not None:
            base_filters.append(OrderAssignment.assigned_to == telecaller_id)

        status_stmt = (
            select(OrderAssignment.assigned_to, OrderAssignment.current_status, func.count())
            .where(*base_filters)
            .group_by(OrderAssignment.assigned_to, OrderAssignment.current_status)
        )
        status_rows = (await self.session.execute(status_stmt)).all()

        follow_up_stmt = (
            select(OrderAssignment.assigned_to, func.count())
            .where(*base_filters, OrderAssignment.next_follow_up_at.is_not(None))
            .group_by(OrderAssignment.assigned_to)
        )
        follow_up_rows = (await self.session.execute(follow_up_stmt)).all()

        by_telecaller: dict[uuid.UUID, dict[str, int]] = {}
        for assigned_to, status, count in status_rows:
            by_telecaller.setdefault(assigned_to, {})[status.value] = count
        for assigned_to, count in follow_up_rows:
            by_telecaller.setdefault(assigned_to, {})["_follow_ups"] = count
        return list(by_telecaller.items())


class CallAttemptRepository(AppendOnlyRepository[CallAttempt]):
    model = CallAttempt

    async def list_for_order(self, order_id: uuid.UUID) -> list[CallAttempt]:
        """Reverse-chronological — the Telecaller order-detail page shows
        the most recent attempt first (spec: "Attempt #3" listed above
        "Attempt #2" above "Attempt #1").
        """
        stmt = (
            select(CallAttempt)
            .where(CallAttempt.order_id == order_id)
            .order_by(CallAttempt.attempt_number.desc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def next_attempt_number(self, order_id: uuid.UUID) -> int:
        stmt = select(func.max(CallAttempt.attempt_number)).where(CallAttempt.order_id == order_id)
        current_max = await self.session.scalar(stmt)
        return (current_max or 0) + 1

    async def list_for_telecaller(
        self, telecaller_id: uuid.UUID, *, limit: int = 200
    ) -> list[tuple[CallAttempt, str]]:
        """Every call this telecaller has personally made, across every
        order they've ever worked — backs the "Call History" nav page.
        Joins in `Order.order_number` directly (rather than a second
        lookup per row) since that's the only order-identifying detail
        this list needs to show.
        """
        stmt = (
            select(CallAttempt, Order.order_number)
            .join(Order, Order.id == CallAttempt.order_id)
            .where(CallAttempt.telecaller_id == telecaller_id)
            .order_by(CallAttempt.attempted_at.desc())
            .limit(limit)
        )
        rows = (await self.session.execute(stmt)).all()
        return [(row[0], row[1]) for row in rows]

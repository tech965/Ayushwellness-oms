from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Select, and_, case, func, or_, select
from sqlalchemy.orm import aliased, selectinload

from app.models.abandoned_checkout import AbandonedCheckout
from app.models.enums import AssignmentStatus, FulfillmentStatus, LeadCategory, PaymentType
from app.models.order import Order, OrderItem
from app.models.telecalling import (
    CallAttempt,
    CheckoutAssignment,
    CheckoutCallAttempt,
    OrderAssignment,
)
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
            .options(
                selectinload(Order.customer),
                selectinload(Order.items),
                selectinload(active_assignment.telecaller),
            )
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

    async def list_category_pool(
        self,
        *,
        team_leader_id: uuid.UUID | None,
        category: LeadCategory | None,
        call_status: str | None,
        date_from: datetime | None,
        date_to: datetime | None,
        page_params: PageParams,
    ) -> tuple[list[tuple[Order, OrderAssignment | None]], int]:
        """The widened Admin/Manager Lead Pool (spec: "COD Fulfilled and
        Prepaid orders become assignable through that same pool, filtered
        by category") — every COD Unfulfilled / COD Fulfilled / Prepaid
        order that's either unassigned or already on the caller's own
        team. `PaymentType.OTHER` is always excluded (see
        `app.services.lead_classification.classify_order`) — it isn't one
        of the spec's defined categories. Same LEFT JOIN shape as
        `list_unfulfilled_pool` (which stays untouched, still backing the
        original `/team/orders/unfulfilled` page) — a NULL assignment side
        means "unassigned", never "dropped".
        """
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
            .where(Order.payment_type.in_([PaymentType.COD, PaymentType.PREPAID]))
            .options(
                selectinload(Order.customer),
                selectinload(Order.items),
                selectinload(active_assignment.telecaller),
            )
        )
        if category == LeadCategory.COD_UNFULFILLED:
            stmt = stmt.where(
                Order.payment_type == PaymentType.COD,
                Order.fulfillment_status == FulfillmentStatus.UNFULFILLED,
            )
        elif category == LeadCategory.COD_FULFILLED:
            stmt = stmt.where(
                Order.payment_type == PaymentType.COD,
                Order.fulfillment_status != FulfillmentStatus.UNFULFILLED,
            )
        elif category == LeadCategory.PREPAID:
            stmt = stmt.where(Order.payment_type == PaymentType.PREPAID)

        if team_leader_id is not None:
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

    async def category_counts(
        self, *, team_leader_id: uuid.UUID | None = None, assigned_to: uuid.UUID | None = None
    ) -> dict[str, dict[str, int]]:
        """One grouped query, not three — total and unassigned counts per
        order-based `LeadCategory`, for the Admin/Manager dashboard's
        "COD Unfulfilled" / "COD Fulfilled" / "Prepaid" tiles (spec:
        real persisted counts, never fabricated numbers). Team-scoped the
        same way `list_category_pool` is: unassigned-anywhere-in-the-pool
        plus assigned-within-this-team only. `assigned_to` narrows to one
        telecaller's own assigned orders instead (for their own dashboard
        summary) — `unassigned` is always 0 in that mode, since every row
        counted is, by definition, assigned to that telecaller.
        """
        active_assignment = aliased(OrderAssignment)
        is_unassigned = active_assignment.id.is_(None).label("is_unassigned")
        stmt = (
            select(Order.payment_type, Order.fulfillment_status, is_unassigned, func.count())
            .select_from(Order)
            .outerjoin(
                active_assignment,
                and_(
                    active_assignment.order_id == Order.id,
                    active_assignment.assignment_status == AssignmentStatus.ACTIVE,
                ),
            )
            .where(Order.payment_type.in_([PaymentType.COD, PaymentType.PREPAID]))
        )
        if assigned_to is not None:
            stmt = stmt.where(active_assignment.assigned_to == assigned_to)
        elif team_leader_id is not None:
            stmt = stmt.where(
                or_(
                    active_assignment.id.is_(None),
                    active_assignment.team_leader_id == team_leader_id,
                )
            )
        stmt = stmt.group_by(Order.payment_type, Order.fulfillment_status, is_unassigned)
        rows = (await self.session.execute(stmt)).all()

        order_categories = (
            LeadCategory.COD_UNFULFILLED,
            LeadCategory.COD_FULFILLED,
            LeadCategory.PREPAID,
        )
        result: dict[str, dict[str, int]] = {
            c.value: {"total": 0, "unassigned": 0} for c in order_categories
        }
        for payment_type, fulfillment_status, count, is_unassigned_flag in rows:
            # `payment_type` is always COD or PREPAID here (the query's own
            # WHERE clause above), so this is never the `PaymentType.OTHER`
            # case `app.services.lead_classification.classify_order` (this
            # repository's counterpart, not imported here to keep this
            # layer free of a dependency on the service layer) treats as
            # uncategorized.
            is_unfulfilled = fulfillment_status == FulfillmentStatus.UNFULFILLED
            category = (
                LeadCategory.COD_UNFULFILLED
                if payment_type == PaymentType.COD and is_unfulfilled
                else LeadCategory.COD_FULFILLED
                if payment_type == PaymentType.COD
                else LeadCategory.PREPAID
            )
            bucket = result[category.value]
            bucket["total"] += count
            if is_unassigned_flag:
                bucket["unassigned"] += count
        return result

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
        from app.models.product import ProductVariant

        return (
            select(OrderAssignment)
            .join(Order, Order.id == OrderAssignment.order_id)
            .where(OrderAssignment.assignment_status == AssignmentStatus.ACTIVE)
            .options(
                selectinload(OrderAssignment.order).selectinload(Order.customer),
                selectinload(OrderAssignment.order)
                .selectinload(Order.items)
                .selectinload(OrderItem.product_variant)
                .selectinload(ProductVariant.product),
                selectinload(OrderAssignment.order).selectinload(Order.shipments),
                selectinload(OrderAssignment.telecaller),
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

    async def total_attempt_count(self, *, telecaller_id: uuid.UUID) -> int:
        """Real total call-attempt count across every order currently
        assigned to this telecaller — `sum(attempt_count)`, distinct from
        the per-order "called/not called" breakdown `telecaller_performance`
        gives (an order called 3 times still only counts once there).
        `attempt_count` itself already reflects edits/deletes correctly
        (see `TelecallingService._recompute_assignment_from_attempts`), so
        this stays accurate without re-deriving it from `CallAttempt` rows.
        """
        stmt = select(func.coalesce(func.sum(OrderAssignment.attempt_count), 0)).where(
            OrderAssignment.assignment_status == AssignmentStatus.ACTIVE,
            OrderAssignment.assigned_to == telecaller_id,
        )
        return int(await self.session.scalar(stmt) or 0)

    async def attempt_averaging_stats(self, *, telecaller_id: uuid.UUID) -> tuple[int, int]:
        """`(total_attempts, orders_with_attempts)` for "Average Call
        Attempts" (`total_attempts / orders_with_attempts`) -- same scope
        as `total_attempt_count` (currently ACTIVE assignments for this
        telecaller, all-time, no date filter -- the dashboard's stat
        tiles have never been date-scoped) and the same correction-
        resolved `attempt_count` column, so the average is never inflated
        by a since-edited/deleted attempt. `orders_with_attempts` counts
        only assignments that have actually been called at least once
        (`attempt_count > 0`) -- an order still sitting at 0 attempts
        would otherwise silently drag the average down even though
        nobody has called it yet.
        """
        stmt = select(
            func.coalesce(func.sum(OrderAssignment.attempt_count), 0),
            func.count(case((OrderAssignment.attempt_count > 0, 1))),
        ).where(
            OrderAssignment.assignment_status == AssignmentStatus.ACTIVE,
            OrderAssignment.assigned_to == telecaller_id,
        )
        total_attempts, orders_with_attempts = (await self.session.execute(stmt)).one()
        return int(total_attempts or 0), int(orders_with_attempts or 0)

    async def fulfilled_counts(
        self, *, team_leader_id: uuid.UUID | None = None, telecaller_id: uuid.UUID | None = None
    ) -> dict[uuid.UUID, int]:
        """How many of a telecaller's *currently* assigned orders are
        *currently* `Order.fulfillment_status == FULFILLED` — a live
        snapshot, not a historical "fulfilled on day X" count (no
        reliable fulfillment-timestamp exists anywhere in this schema; see
        `TelecallingService.telecaller_detail_summary`'s docstring).
        Deliberately never conflated with `TelecallingStatus.CONFIRMED` —
        a call outcome and an actual fulfillment are separate facts about
        an order.
        """
        base_filters = [OrderAssignment.assignment_status == AssignmentStatus.ACTIVE]
        if team_leader_id is not None:
            base_filters.append(OrderAssignment.team_leader_id == team_leader_id)
        if telecaller_id is not None:
            base_filters.append(OrderAssignment.assigned_to == telecaller_id)

        stmt = (
            select(OrderAssignment.assigned_to, func.count())
            .select_from(OrderAssignment)
            .join(Order, Order.id == OrderAssignment.order_id)
            .where(*base_filters, Order.fulfillment_status == FulfillmentStatus.FULFILLED)
            .group_by(OrderAssignment.assigned_to)
        )
        rows = (await self.session.execute(stmt)).all()
        result: dict[uuid.UUID, int] = {}
        for assigned_to, count in rows:
            result[assigned_to] = count
        return result


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

    async def resolve_current_for_order(
        self, order_id: uuid.UUID, *, include_void: bool = False
    ) -> list[CallAttempt]:
        """Resolves every `attempt_number` for this order to its *current*
        row — the original, or its latest correction if it's been edited
        or deleted (see `CallAttempt`'s docstring). Reverse-chronological
        by `attempt_number`, same ordering as `list_for_order`.

        Resolution is by chain structure, not by comparing `created_at`:
        the "current" row for an `attempt_number` is whichever row is
        never itself referenced by another row's `corrects_attempt_id` —
        the one tip of that correction chain. This is exact regardless of
        timestamp precision (SQLite's `CURRENT_TIMESTAMP` default is
        whole-second, so an attempt corrected within the same second as
        it was created would tie under a `created_at` comparison — this
        approach has no such edge case). A per-order fetch-then-resolve-
        in-Python (never a DB-side `DISTINCT ON`) — an order realistically
        has a handful of call attempts, never enough for this to be a
        performance concern, and staying in Python keeps this dialect-
        portable (SQLite in tests, Postgres in production).
        """
        all_rows = await self.list_for_order(order_id)
        corrected_ids = {r.corrects_attempt_id for r in all_rows if r.corrects_attempt_id}
        resolved = sorted(
            (r for r in all_rows if r.id not in corrected_ids),
            key=lambda r: r.attempt_number,
            reverse=True,
        )
        if not include_void:
            resolved = [r for r in resolved if not r.is_void]
        return resolved

    async def get_correctable_for_order(
        self, attempt_id: uuid.UUID, order_id: uuid.UUID
    ) -> CallAttempt | None:
        """The *current* row for whichever `attempt_number` `attempt_id`
        belongs to — resolving via `attempt_number` (not returning
        `attempt_id`'s row itself) means editing/deleting always applies
        to the latest state, so two edits made in quick succession both
        land on top of each other correctly instead of one silently
        clobbering based on a stale id. Returns `None` if `attempt_id`
        doesn't belong to this order, or the attempt is already void.
        """
        target = await self.get_by_id(attempt_id)
        if target is None or target.order_id != order_id:
            return None
        current = next(
            (
                r
                for r in await self.resolve_current_for_order(order_id, include_void=True)
                if r.attempt_number == target.attempt_number
            ),
            None,
        )
        if current is None or current.is_void:
            return None
        return current

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

    async def list_resolved_for_telecaller_in_range(
        self, telecaller_id: uuid.UUID, *, date_from: datetime, date_to: datetime
    ) -> list[CallAttempt]:
        """Every *currently active* (non-void, latest-corrected) call
        attempt this telecaller made with `attempted_at` in
        `[date_from, date_to]` — backs the daily performance graph. An
        edit preserves the original `attempted_at` (see
        `TelecallingService.edit_call_attempt`), so filtering on
        `attempted_at` here always keeps a corrected attempt in the same
        day-bucket its call actually happened in, never the day it was
        edited. Resolution is chain-tip-based, same as
        `resolve_current_for_order` (see its docstring for why, not a
        `created_at` comparison) — just across every order for one
        telecaller instead of one order.

        Known limitation: filters on `CallAttempt.telecaller_id`, so if an
        order is reassigned to a *different* telecaller who then edits or
        deletes an attempt originally logged by this telecaller, that
        correction row (owned by the new telecaller) won't be fetched
        here, and this method would still show the pre-correction state.
        `calls.manage` only ever lets the *currently* assigned telecaller
        correct an attempt, so this only diverges when a reassignment
        happens between the original call and a later correction of it —
        rare, and not a permission or crash risk, just a display lag for
        that one edge case.
        """
        stmt = (
            select(CallAttempt)
            .where(
                CallAttempt.telecaller_id == telecaller_id,
                CallAttempt.attempted_at >= date_from,
                CallAttempt.attempted_at <= date_to,
            )
        )
        rows = list((await self.session.execute(stmt)).scalars().all())
        corrected_ids = {r.corrects_attempt_id for r in rows if r.corrects_attempt_id}
        return [r for r in rows if r.id not in corrected_ids and not r.is_void]


class CheckoutAssignmentRepository(BaseRepository[CheckoutAssignment]):
    """`OrderAssignmentRepository`'s exact counterpart for
    `CheckoutAssignment` — see `app.models.telecalling.CheckoutAssignment`'s
    docstring for why this is a separate table/repository rather than a
    polymorphic extension of `OrderAssignment`.
    """

    model = CheckoutAssignment

    async def get_active_for_checkout(self, checkout_id: uuid.UUID) -> CheckoutAssignment | None:
        stmt = select(CheckoutAssignment).where(
            CheckoutAssignment.checkout_id == checkout_id,
            CheckoutAssignment.assignment_status == AssignmentStatus.ACTIVE,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    def _base_scope_query(self) -> Select:
        return (
            select(CheckoutAssignment)
            .join(AbandonedCheckout, AbandonedCheckout.id == CheckoutAssignment.checkout_id)
            .where(CheckoutAssignment.assignment_status == AssignmentStatus.ACTIVE)
            .options(
                selectinload(CheckoutAssignment.checkout).selectinload(AbandonedCheckout.customer),
                selectinload(CheckoutAssignment.telecaller),
            )
        )

    def search_query(
        self,
        *,
        assigned_to: uuid.UUID | None = None,
        team_leader_id: uuid.UUID | None = None,
        call_status: str | None = None,
        follow_up_from: datetime | None = None,
        follow_up_to: datetime | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
    ) -> Select:
        stmt = self._base_scope_query()
        if assigned_to is not None:
            stmt = stmt.where(CheckoutAssignment.assigned_to == assigned_to)
        if team_leader_id is not None:
            stmt = stmt.where(CheckoutAssignment.team_leader_id == team_leader_id)
        if call_status:
            stmt = stmt.where(CheckoutAssignment.current_status == call_status)
        if follow_up_from is not None:
            stmt = stmt.where(CheckoutAssignment.next_follow_up_at >= follow_up_from)
        if follow_up_to is not None:
            stmt = stmt.where(CheckoutAssignment.next_follow_up_at < follow_up_to)
        if date_from is not None:
            stmt = stmt.where(AbandonedCheckout.checkout_created_at >= date_from)
        if date_to is not None:
            stmt = stmt.where(AbandonedCheckout.checkout_created_at <= date_to)
        return stmt

    async def get_scoped(
        self,
        checkout_id: uuid.UUID,
        *,
        assigned_to: uuid.UUID | None,
        team_leader_id: uuid.UUID | None,
    ) -> CheckoutAssignment | None:
        stmt = self._base_scope_query().where(CheckoutAssignment.checkout_id == checkout_id)
        if assigned_to is not None:
            stmt = stmt.where(CheckoutAssignment.assigned_to == assigned_to)
        if team_leader_id is not None:
            stmt = stmt.where(CheckoutAssignment.team_leader_id == team_leader_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_pool(
        self,
        *,
        team_leader_id: uuid.UUID | None,
        call_status: str | None,
        date_from: datetime | None,
        date_to: datetime | None,
        page_params: PageParams,
    ) -> tuple[list[tuple[AbandonedCheckout, CheckoutAssignment | None]], int]:
        """The Admin/Manager Abandoned Checkout pool — every open
        (`is_recovered=False`), *contactable* (real phone or email —
        spec: "never turn anonymous cart activity into a telecalling
        lead") checkout that's unassigned or already on the caller's own
        team. Same LEFT JOIN / unassigned-or-own-team shape as
        `OrderAssignmentRepository.list_unfulfilled_pool`.
        """
        active_assignment = aliased(CheckoutAssignment)
        stmt = (
            select(AbandonedCheckout, active_assignment)
            .outerjoin(
                active_assignment,
                and_(
                    active_assignment.checkout_id == AbandonedCheckout.id,
                    active_assignment.assignment_status == AssignmentStatus.ACTIVE,
                ),
            )
            .where(
                AbandonedCheckout.is_recovered.is_(False),
                or_(
                    AbandonedCheckout.customer_phone.is_not(None),
                    AbandonedCheckout.customer_email.is_not(None),
                ),
            )
            .options(
                selectinload(AbandonedCheckout.customer), selectinload(active_assignment.telecaller)
            )
        )
        if team_leader_id is not None:
            stmt = stmt.where(
                or_(
                    active_assignment.id.is_(None),
                    active_assignment.team_leader_id == team_leader_id,
                )
            )
        if call_status:
            stmt = stmt.where(active_assignment.current_status == call_status)
        if date_from is not None:
            stmt = stmt.where(AbandonedCheckout.checkout_created_at >= date_from)
        if date_to is not None:
            stmt = stmt.where(AbandonedCheckout.checkout_created_at <= date_to)

        total = await self.session.scalar(select(func.count()).select_from(stmt.subquery()))
        stmt = (
            stmt.order_by(AbandonedCheckout.checkout_created_at.desc())
            .offset(page_params.offset)
            .limit(page_params.page_size)
        )
        rows = (await self.session.execute(stmt)).all()
        return [(row[0], row[1]) for row in rows], total or 0

    async def pool_counts(self, *, team_leader_id: uuid.UUID | None) -> dict[str, int]:
        """Total/unassigned counts for the dashboard's "Abandoned
        Checkouts" tile — same contactable/open filter as `list_pool`.
        """
        active_assignment = aliased(CheckoutAssignment)
        unassigned_flag = case((active_assignment.id.is_(None), 1), else_=0)
        stmt = (
            select(func.count(), func.sum(unassigned_flag))
            .select_from(AbandonedCheckout)
            .outerjoin(
                active_assignment,
                and_(
                    active_assignment.checkout_id == AbandonedCheckout.id,
                    active_assignment.assignment_status == AssignmentStatus.ACTIVE,
                ),
            )
            .where(
                AbandonedCheckout.is_recovered.is_(False),
                or_(
                    AbandonedCheckout.customer_phone.is_not(None),
                    AbandonedCheckout.customer_email.is_not(None),
                ),
            )
        )
        if team_leader_id is not None:
            stmt = stmt.where(
                or_(
                    active_assignment.id.is_(None),
                    active_assignment.team_leader_id == team_leader_id,
                )
            )
        total, unassigned = (await self.session.execute(stmt)).one()
        return {"total": total or 0, "unassigned": int(unassigned or 0)}

    async def list_for_checkout(self, checkout_id: uuid.UUID) -> list[CheckoutAssignment]:
        stmt = (
            select(CheckoutAssignment)
            .where(CheckoutAssignment.checkout_id == checkout_id)
            .order_by(CheckoutAssignment.assigned_at.asc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def team_summary_counts(self, *, team_leader_id: uuid.UUID | None) -> dict[str, int]:
        stmt = select(CheckoutAssignment.current_status, func.count()).where(
            CheckoutAssignment.assignment_status == AssignmentStatus.ACTIVE
        )
        if team_leader_id is not None:
            stmt = stmt.where(CheckoutAssignment.team_leader_id == team_leader_id)
        stmt = stmt.group_by(CheckoutAssignment.current_status)
        rows = (await self.session.execute(stmt)).all()
        return {status.value: count for status, count in rows}

    async def telecaller_performance(
        self, *, team_leader_id: uuid.UUID | None, telecaller_id: uuid.UUID | None = None
    ) -> list[tuple[uuid.UUID, dict[str, int]]]:
        base_filters = [CheckoutAssignment.assignment_status == AssignmentStatus.ACTIVE]
        if team_leader_id is not None:
            base_filters.append(CheckoutAssignment.team_leader_id == team_leader_id)
        if telecaller_id is not None:
            base_filters.append(CheckoutAssignment.assigned_to == telecaller_id)

        status_stmt = (
            select(CheckoutAssignment.assigned_to, CheckoutAssignment.current_status, func.count())
            .where(*base_filters)
            .group_by(CheckoutAssignment.assigned_to, CheckoutAssignment.current_status)
        )
        status_rows = (await self.session.execute(status_stmt)).all()

        follow_up_stmt = (
            select(CheckoutAssignment.assigned_to, func.count())
            .where(*base_filters, CheckoutAssignment.next_follow_up_at.is_not(None))
            .group_by(CheckoutAssignment.assigned_to)
        )
        follow_up_rows = (await self.session.execute(follow_up_stmt)).all()

        by_telecaller: dict[uuid.UUID, dict[str, int]] = {}
        for assigned_to, status, count in status_rows:
            by_telecaller.setdefault(assigned_to, {})[status.value] = count
        for assigned_to, count in follow_up_rows:
            by_telecaller.setdefault(assigned_to, {})["_follow_ups"] = count
        return list(by_telecaller.items())


class CheckoutCallAttemptRepository(AppendOnlyRepository[CheckoutCallAttempt]):
    model = CheckoutCallAttempt

    async def list_for_checkout(self, checkout_id: uuid.UUID) -> list[CheckoutCallAttempt]:
        stmt = (
            select(CheckoutCallAttempt)
            .where(CheckoutCallAttempt.checkout_id == checkout_id)
            .order_by(CheckoutCallAttempt.attempt_number.desc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def next_attempt_number(self, checkout_id: uuid.UUID) -> int:
        stmt = select(func.max(CheckoutCallAttempt.attempt_number)).where(
            CheckoutCallAttempt.checkout_id == checkout_id
        )
        current_max = await self.session.scalar(stmt)
        return (current_max or 0) + 1

    async def list_for_telecaller(
        self, telecaller_id: uuid.UUID, *, limit: int = 200
    ) -> list[CheckoutCallAttempt]:
        """Mirrors `CallAttemptRepository.list_for_telecaller` — there's
        no human-readable "checkout number" the way an order has
        `order_number`, so unlike that method this doesn't join in a
        display label; `CheckoutCallAttempt.checkout_id` is already on
        the returned row.
        """
        stmt = (
            select(CheckoutCallAttempt)
            .where(CheckoutCallAttempt.telecaller_id == telecaller_id)
            .order_by(CheckoutCallAttempt.attempted_at.desc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

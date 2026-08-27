"""Team Leader / Telecaller order-assignment and calling workflow.

This is the first row-level-scoping feature in the codebase (every other
`*.read` permission is all-or-nothing across its whole table). The single
rule every method here follows: the scope filter (`assigned_to`/
`team_leader_id`) is always derived from the authenticated `actor` passed
in by the endpoint layer — never from a client-supplied id — so a
Telecaller changing an order id in the URL, or a Team Leader guessing
another team's telecaller id, gets `AuthorizationError` (403), not another
tenant's data.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AuthorizationError, ConflictError, NotFoundError, ValidationError
from app.core.timezone import ist_day_bounds
from app.models.auth import User
from app.models.enums import AssignmentStatus, TelecallingStatus
from app.models.telecalling import CallAttempt, OrderAssignment
from app.repositories.auth import UserRepository
from app.repositories.order import OrderRepository
from app.repositories.telecalling import CallAttemptRepository, OrderAssignmentRepository
from app.schemas.common import PageParams, SortParams
from app.services.audit_service import AuditService


class ScopeFilter:
    """Resolved row-scope for one request: exactly one of `assigned_to`/
    `team_leader_id` is set for a Telecaller/Team Leader; both `None` (no
    filter at all) for an admin.
    """

    def __init__(self, *, assigned_to: uuid.UUID | None, team_leader_id: uuid.UUID | None) -> None:
        self.assigned_to = assigned_to
        self.team_leader_id = team_leader_id


def resolve_team_scope(actor: User) -> ScopeFilter:
    """Scope for `/team/*` endpoints — a Team Leader sees only their own
    team; an admin (is_superuser) sees everything.
    """
    if actor.is_superuser:
        return ScopeFilter(assigned_to=None, team_leader_id=None)
    return ScopeFilter(assigned_to=None, team_leader_id=actor.id)


def resolve_telecaller_scope(actor: User) -> ScopeFilter:
    """Scope for `/telecaller/*` endpoints — always the caller's own
    assignments. Even an admin hitting these routes is scoped to
    themselves (there's no legitimate reason for `/telecaller/orders` to
    return anything but "my own orders" for anyone); an admin who wants
    team-wide or global visibility uses `/team/*`/`/orders` instead.
    """
    return ScopeFilter(assigned_to=actor.id, team_leader_id=None)


class TelecallingService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.assignments = OrderAssignmentRepository(session)
        self.call_attempts = CallAttemptRepository(session)
        self.orders = OrderRepository(session)
        self.users = UserRepository(session)
        self.audit = AuditService(session)

    # ------------------------------------------------------------------
    # Listing / detail (read paths — every one takes a `scope`, never a
    # raw telecaller/team id from the caller)
    # ------------------------------------------------------------------

    async def list_assignments(
        self,
        *,
        scope: ScopeFilter,
        page_params: PageParams,
        sort_params: SortParams,
        unfulfilled_only: bool = False,
        call_status: str | None = None,
        when: str | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
    ) -> tuple[list[OrderAssignment], int]:
        follow_up_from, follow_up_to = _follow_up_window(when)
        query = self.assignments.search_query(
            assigned_to=scope.assigned_to,
            team_leader_id=scope.team_leader_id,
            unfulfilled_only=unfulfilled_only,
            call_status=call_status,
            follow_up_from=follow_up_from,
            follow_up_to=follow_up_to,
            date_from=date_from,
            date_to=date_to,
        )
        items, total = await self.assignments.list(
            page_params=page_params,
            sort_params=sort_params,
            query=query,
            default_sort_column="created_at",
        )
        return list(items), total

    async def list_unfulfilled_pool(
        self,
        *,
        scope: ScopeFilter,
        page_params: PageParams,
        call_status: str | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
    ):
        return await self.assignments.list_unfulfilled_pool(
            team_leader_id=scope.team_leader_id,
            call_status=call_status,
            date_from=date_from,
            date_to=date_to,
            page_params=page_params,
        )

    async def get_scoped_assignment(
        self, order_id: uuid.UUID, *, scope: ScopeFilter
    ) -> OrderAssignment:
        assignment = await self.assignments.get_scoped(
            order_id, assigned_to=scope.assigned_to, team_leader_id=scope.team_leader_id
        )
        if assignment is None:
            # Deliberately the same error whether the order doesn't exist,
            # has no active assignment, or exists but belongs to someone
            # else's scope — distinguishing those would let a client
            # fingerprint which order ids exist outside their own access.
            raise AuthorizationError("Order not found or not assigned to you.")
        return assignment

    async def list_call_history(
        self, order_id: uuid.UUID, *, scope: ScopeFilter
    ) -> list[CallAttempt]:
        await self.get_scoped_assignment(order_id, scope=scope)
        return await self.call_attempts.list_for_order(order_id)

    async def list_my_call_history(self, telecaller_id: uuid.UUID) -> list[tuple[CallAttempt, str]]:
        """Every call `telecaller_id` has personally made, across every
        order — always hard-scoped to the caller's own id by the endpoint
        layer, never a client-supplied telecaller id.
        """
        return await self.call_attempts.list_for_telecaller(telecaller_id)

    async def team_summary(self, *, scope: ScopeFilter) -> dict[str, int]:
        counts = await self.assignments.team_summary_counts(team_leader_id=scope.team_leader_id)
        return _summary_from_counts(counts, await self._follow_ups_today_count(scope))

    async def telecaller_summary(self, *, scope: ScopeFilter) -> dict[str, int]:
        # `team_summary_counts` only supports team_leader-scoped grouping,
        # so a single telecaller's own summary re-derives counts here
        # instead — a dedicated single-telecaller aggregate query.
        query = self.assignments.search_query(assigned_to=scope.assigned_to)
        rows = (
            (await self.session.execute(query.with_only_columns(OrderAssignment.current_status)))
            .scalars()
            .all()
        )
        counts: dict[str, int] = {}
        for status in rows:
            counts[status.value] = counts.get(status.value, 0) + 1
        return _summary_from_counts(counts, await self._follow_ups_today_count(scope))

    async def _follow_ups_today_count(self, scope: ScopeFilter) -> int:
        from sqlalchemy import func, select

        start, end = ist_day_bounds()
        query = self.assignments.search_query(
            assigned_to=scope.assigned_to,
            team_leader_id=scope.team_leader_id,
            follow_up_from=start,
            follow_up_to=end,
        )
        total = await self.session.scalar(select(func.count()).select_from(query.subquery()))
        return total or 0

    async def team_telecaller_performance(self, *, scope: ScopeFilter) -> list[dict]:
        breakdown = await self.assignments.telecaller_performance(
            team_leader_id=scope.team_leader_id
        )
        results = []
        for telecaller_id, counts in breakdown:
            telecaller = await self.users.get_by_id(telecaller_id)
            results.append(
                {
                    "telecaller_id": telecaller_id,
                    "telecaller_name": telecaller.name if telecaller else "Unknown",
                    **_performance_from_counts(counts),
                }
            )
        return results

    async def assert_telecaller_in_team_scope(
        self, telecaller_id: uuid.UUID, *, actor: User
    ) -> User:
        telecaller = await self.users.get_with_permissions(telecaller_id)
        if telecaller is None or "TELECALLER" not in telecaller.role_names:
            raise NotFoundError("Telecaller not found.")
        if not actor.is_superuser and telecaller.team_leader_id != actor.id:
            raise AuthorizationError("That telecaller is not on your team.")
        return telecaller

    # ------------------------------------------------------------------
    # Assignment mutations
    # ------------------------------------------------------------------

    async def assign_orders(
        self,
        *,
        order_ids: list[uuid.UUID],
        mode: str,
        telecaller_id: uuid.UUID | None,
        telecaller_ids: list[uuid.UUID] | None,
        actor: User,
    ) -> list[OrderAssignment]:
        if mode == "manual":
            if telecaller_id is None:
                raise ValidationError("telecaller_id is required for manual assignment.")
            candidate_ids: list[uuid.UUID] = [telecaller_id]
        else:
            candidate_ids = telecaller_ids or []
        telecallers = await self._resolve_and_validate_telecallers(candidate_ids, actor=actor)

        # Atomic, all-or-nothing duplicate check: an already-actively-
        # assigned order must never be silently reassigned by a plain
        # "assign" call — the caller must use the explicit reassign
        # action instead ("an already assigned order must not silently
        # be assigned again").
        conflicts: list[str] = []
        for order_id in order_ids:
            order = await self.orders.get_by_id(order_id)
            if order is None:
                raise NotFoundError(f"Order {order_id} not found.")
            if await self.assignments.get_active_for_order(order_id) is not None:
                conflicts.append(str(order_id))
        if conflicts:
            raise ConflictError(
                "The following orders already have an active assignment — use reassign instead: "
                + ", ".join(conflicts),
                details={"order_ids": conflicts},
            )

        now = datetime.now(UTC)
        created: list[OrderAssignment] = []
        for index, order_id in enumerate(order_ids):
            # Plain round-robin: `telecallers[i % len(telecallers)]`. For N
            # orders over K telecallers this always yields bucket sizes of
            # floor(N/K) or ceil(N/K) — e.g. 100 orders / 6 telecallers ->
            # four telecallers get 17, two get 16, matching the spec's
            # worked example exactly.
            telecaller = telecallers[index % len(telecallers)]
            assignment = await self.assignments.create(
                order_id=order_id,
                assigned_to=telecaller.id,
                assigned_by=actor.id,
                assigned_at=now,
                team_leader_id=telecaller.team_leader_id,
                assignment_status=AssignmentStatus.ACTIVE,
                current_status=TelecallingStatus.NOT_CALLED,
            )
            created.append(assignment)
            await self.audit.record(
                user=actor,
                action="order.assigned",
                entity_type="order",
                entity_id=str(order_id),
                new_value={"assigned_to": str(telecaller.id), "mode": mode},
            )

        await self.session.commit()
        return created

    async def reassign_order(
        self, *, order_id: uuid.UUID, new_telecaller_id: uuid.UUID, reason: str, actor: User
    ) -> OrderAssignment:
        current = await self.assignments.get_active_for_order(order_id)
        if current is None:
            raise NotFoundError("Order has no active assignment to reassign — use assign instead.")

        (new_telecaller,) = await self._resolve_and_validate_telecallers(
            [new_telecaller_id], actor=actor
        )
        # A Team Leader may only reassign orders that are already on
        # their own team — an admin bypasses this.
        if not actor.is_superuser and current.team_leader_id != actor.id:
            raise AuthorizationError("That order is not on your team.")

        previous_telecaller_id = current.assigned_to
        now = datetime.now(UTC)

        await self.assignments.update(current, assignment_status=AssignmentStatus.INACTIVE)

        new_assignment = await self.assignments.create(
            order_id=order_id,
            assigned_to=new_telecaller.id,
            assigned_by=actor.id,
            assigned_at=now,
            team_leader_id=new_telecaller.team_leader_id,
            assignment_status=AssignmentStatus.ACTIVE,
            reassigned_from=previous_telecaller_id,
            reassigned_to=new_telecaller.id,
            reassigned_at=now,
            reassignment_reason=reason,
            current_status=TelecallingStatus.NOT_CALLED,
        )

        await self.audit.record(
            user=actor,
            action="order.reassigned",
            entity_type="order",
            entity_id=str(order_id),
            previous_value={"telecaller_id": str(previous_telecaller_id)},
            new_value={"telecaller_id": str(new_telecaller.id), "reason": reason},
        )
        await self.session.commit()
        return new_assignment

    async def _resolve_and_validate_telecallers(
        self, telecaller_ids: list[uuid.UUID], *, actor: User
    ) -> list[User]:
        if not telecaller_ids:
            raise ValidationError("At least one telecaller must be specified.")
        resolved = []
        for telecaller_id in telecaller_ids:
            resolved.append(await self.assert_telecaller_in_team_scope(telecaller_id, actor=actor))
        return resolved

    # ------------------------------------------------------------------
    # Calling / follow-up mutations (Telecaller-only — a Team Leader has
    # no `calls.manage` permission, so never reaches these; the extra
    # `assigned_to == actor.id` check here is defense-in-depth, not the
    # only guard).
    # ------------------------------------------------------------------

    async def log_call(
        self,
        order_id: uuid.UUID,
        *,
        outcome: TelecallingStatus,
        notes: str | None,
        next_follow_up_at: datetime | None,
        actor: User,
    ) -> CallAttempt:
        assignment = await self.assignments.get_active_for_order(order_id)
        if assignment is None:
            raise NotFoundError("Order is not currently assigned.")
        if not actor.is_superuser and assignment.assigned_to != actor.id:
            raise AuthorizationError("This order is not assigned to you.")

        attempt_number = await self.call_attempts.next_attempt_number(order_id)
        now = datetime.now(UTC)
        attempt = await self.call_attempts.create(
            order_id=order_id,
            telecaller_id=actor.id,
            attempt_number=attempt_number,
            attempted_at=now,
            outcome=outcome,
            notes=notes,
            next_follow_up_at=next_follow_up_at,
        )
        await self.assignments.update(
            assignment,
            current_status=outcome,
            attempt_count=assignment.attempt_count + 1,
            last_attempt_at=now,
            next_follow_up_at=next_follow_up_at,
        )
        await self.audit.record(
            user=actor,
            action="call.logged",
            entity_type="order",
            entity_id=str(order_id),
            new_value={"outcome": outcome.value, "attempt_number": attempt_number},
        )
        await self.session.commit()
        return attempt

    async def schedule_follow_up(
        self, order_id: uuid.UUID, *, next_follow_up_at: datetime, actor: User
    ) -> OrderAssignment:
        assignment = await self.assignments.get_active_for_order(order_id)
        if assignment is None:
            raise NotFoundError("Order is not currently assigned.")
        if not actor.is_superuser and assignment.assigned_to != actor.id:
            raise AuthorizationError("This order is not assigned to you.")

        previous = assignment.next_follow_up_at
        await self.assignments.update(assignment, next_follow_up_at=next_follow_up_at)
        await self.audit.record(
            user=actor,
            action="followup.scheduled",
            entity_type="order",
            entity_id=str(order_id),
            previous_value={"next_follow_up_at": previous.isoformat() if previous else None},
            new_value={"next_follow_up_at": next_follow_up_at.isoformat()},
        )
        await self.session.commit()
        return assignment


def _follow_up_window(when: str | None) -> tuple[datetime | None, datetime | None]:
    if when is None:
        return None, None
    start, end = ist_day_bounds()
    if when == "today":
        return start, end
    if when == "overdue":
        return None, start
    if when == "upcoming":
        return end, None
    raise ValidationError(f"Unknown follow-up window: {when!r}")


def _performance_from_counts(counts: dict[str, int]) -> dict[str, int]:
    """Shapes one telecaller's status-count breakdown (from
    `OrderAssignmentRepository.telecaller_performance`, including its
    `"_follow_ups"` pseudo-status) into `TelecallerPerformanceResponse`'s
    fields for the Team Leader's performance table.
    """
    counts = dict(counts)
    follow_ups = counts.pop("_follow_ups", 0)
    assigned = sum(counts.values())
    not_called = counts.get(TelecallingStatus.NOT_CALLED.value, 0)
    return {
        "assigned": assigned,
        "called": assigned - not_called,
        "connected": counts.get(TelecallingStatus.CONNECTED.value, 0),
        "follow_ups": follow_ups,
        "confirmed": counts.get(TelecallingStatus.CONFIRMED.value, 0),
        "not_interested": counts.get(TelecallingStatus.NOT_INTERESTED.value, 0),
    }


def _summary_from_counts(counts: dict[str, int], follow_ups_today: int) -> dict[str, int]:
    assigned = sum(counts.values())
    not_called = counts.get(TelecallingStatus.NOT_CALLED.value, 0)
    return {
        "assigned": assigned,
        "pending": not_called,
        "called": assigned - not_called,
        "connected": counts.get(TelecallingStatus.CONNECTED.value, 0),
        "follow_ups_today": follow_ups_today,
        "confirmed": counts.get(TelecallingStatus.CONFIRMED.value, 0),
        "not_interested": counts.get(TelecallingStatus.NOT_INTERESTED.value, 0),
    }

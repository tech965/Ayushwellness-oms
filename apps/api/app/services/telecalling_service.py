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

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import set_committed_value

from app.core.config import settings
from app.core.exceptions import AuthorizationError, ConflictError, NotFoundError, ValidationError
from app.core.logging import get_logger
from app.core.timezone import ist_day_bounds, to_ist
from app.integrations.registry import get_adapter
from app.integrations.shopify.live import require_live_reader
from app.models.auth import User
from app.models.checkout_workflow import CheckoutWorkflow
from app.models.enums import (
    AssignmentStatus,
    FulfillmentStatus,
    LeadCategory,
    TelecallingStatus,
)
from app.models.integration import IntegrationCode
from app.models.order import Order, OrderEvent
from app.models.order_workflow import OrderWorkflow
from app.models.product import ProductVariant
from app.models.telecalling import CallAttempt, CheckoutAssignment, OrderAssignment
from app.repositories.abandoned_checkout import AbandonedCheckoutRepository
from app.repositories.auth import UserRepository
from app.repositories.order import OrderEventRepository, OrderRepository
from app.repositories.telecalling import (
    CallAttemptRepository,
    CheckoutAssignmentRepository,
    CheckoutCallAttemptRepository,
    OrderAssignmentRepository,
)
from app.schemas.common import PageParams, SortParams
from app.services.audit_service import AuditService
from app.services.live_checkouts import (
    collect_checkouts,
    fetch_checkouts_by_ids,
    hydrate_checkout,
)
from app.services.live_orders import LOCAL_PREFIX, get_reader, hydrate_order
from app.services.order_ref import (
    ensure_checkout_workflow,
    ensure_workflow,
    resolve_checkout_ref,
    resolve_ref,
)
from app.services.order_service import OrderService

logger = get_logger(__name__)

# Upper bound on one scope's active assignments loaded for a live listing.
LIVE_ASSIGNMENT_CAP = 1000
# Shopify pages (100 orders each) crawled to build a live lead pool.
LIVE_POOL_PAGES = 20

# "Connected" isn't a `TelecallingStatus` value of its own any more (the
# 2026-09-30 status simplification removed the old literal CONNECTED
# outcome) -- it's now a computed metric: any logged outcome that implies
# the telecaller actually reached/spoke with the customer, i.e. everything
# except "never called"/"didn't pick up"/"line busy"/"phone off". Used by
# both `_performance_from_counts` and `_summary_from_counts` below, and the
# daily-performance bucket loop, so the "Connected" figure on the Team
# Leader dashboards keeps meaning the same real-world thing it always did.
#
# Includes the matching LEGACY "didn't reach the customer" values too
# (NOT_RECEIVED/CALL_ATTEMPTED/INVALID_NUMBER -- see `TelecallingStatus`'s
# docstring for why real production rows can still carry these) so a
# telecaller's historical performance numbers stay accurate; the legacy
# CONNECTED/CALL_BACK_REQUESTED/FOLLOW_UP_REQUIRED values clearly DID
# involve reaching the customer and are deliberately left out of this set.
_NOT_CONNECTED_OUTCOMES = frozenset(
    {
        TelecallingStatus.NOT_CALLED,
        TelecallingStatus.NOT_ANSWERING,
        TelecallingStatus.BUSY,
        TelecallingStatus.SWITCHED_OFF,
        TelecallingStatus.NOT_RECEIVED,
        TelecallingStatus.CALL_ATTEMPTED,
        TelecallingStatus.INVALID_NUMBER,
    }
)


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
    team; an admin (is_superuser, or holding the ADMIN role) sees
    everything.

    `is_superuser` alone isn't sufficient: a real production Admin account
    was found with `is_superuser=False` (role-based ADMIN only), which
    fell through to the Team-Leader branch below — `team_leader_id`
    became that Admin's own id, so `/team/telecallers/roster` filtered to
    `User.team_leader_id == <admin id>` and excluded every telecaller
    reporting to no one/a different leader (confirmed: an active
    TELECALLER with `team_leader_id=None` was silently excluded from an
    Admin's own roster view). `actor.role_names` is already eager-loaded
    by `UserRepository.get_with_permissions` (`user_roles.role...`), so
    this doesn't add a new lazy-load risk.
    """
    if actor.is_superuser or "ADMIN" in actor.role_names:
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
        # Set by the live pool builders when their bounded Shopify crawl
        # stopped early; endpoints surface it as an incomplete-result warning.
        self.last_pool_incomplete = False
        self.call_attempts = CallAttemptRepository(session)
        self.orders = OrderRepository(session)
        self.users = UserRepository(session)
        self.audit = AuditService(session)
        self.order_service = OrderService(session)
        self.checkout_assignments = CheckoutAssignmentRepository(session)
        self.checkout_call_attempts = CheckoutCallAttemptRepository(session)
        self.checkouts = AbandonedCheckoutRepository(session)
        self.order_events = OrderEventRepository(session)

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
        q: str | None = None,
    ) -> tuple[list[OrderAssignment], int]:
        follow_up_from, follow_up_to = _follow_up_window(when)
        if settings.SHOPIFY_LIVE_MODE:
            return await self._list_assignments_live(
                scope=scope,
                page_params=page_params,
                unfulfilled_only=unfulfilled_only,
                call_status=call_status,
                follow_up_from=follow_up_from,
                follow_up_to=follow_up_to,
                date_from=date_from,
                date_to=date_to,
                q=q,
            )
        query = self.assignments.search_query(
            assigned_to=scope.assigned_to,
            team_leader_id=scope.team_leader_id,
            unfulfilled_only=unfulfilled_only,
            call_status=call_status,
            follow_up_from=follow_up_from,
            follow_up_to=follow_up_to,
            date_from=date_from,
            date_to=date_to,
            q=q,
        )
        items, total = await self.assignments.list(
            page_params=page_params,
            sort_params=sort_params,
            query=query,
            default_sort_column="created_at",
        )
        return list(items), total

    async def attach_live_orders(self, assignments: list[OrderAssignment]) -> list[OrderAssignment]:
        """Attaches each assignment's CURRENT order, fetched live from
        Shopify in batched calls, as an in-memory value (never flushed).
        Assignments whose order Shopify no longer returns (and that have
        no local historical row) are omitted from the result."""
        if not assignments:
            return []
        workflows = (
            (
                await self.session.execute(
                    select(OrderWorkflow).where(
                        OrderWorkflow.id.in_({a.order_id for a in assignments})
                    )
                )
            )
            .scalars()
            .all()
        )
        by_id = {w.id: w for w in workflows}
        external_ids = [
            w.external_order_id
            for w in workflows
            if not w.external_order_id.startswith(LOCAL_PREFIX)
        ]
        live = {}
        if external_ids:
            for data in await get_reader().get_orders_by_ids(external_ids):
                live[data["external_id"]] = data
        kept: list[OrderAssignment] = []
        for assignment in assignments:
            workflow = by_id.get(assignment.order_id)
            data = live.get(workflow.external_order_id) if workflow else None  # type: ignore[assignment]
            if data is not None:
                set_committed_value(assignment, "order", hydrate_order(data, workflow))
                kept.append(assignment)
            elif workflow is not None and workflow.external_order_id.startswith(LOCAL_PREFIX):
                local = await self.orders.get_by_id(assignment.order_id)
                if local is not None:
                    set_committed_value(assignment, "order", local)
                    kept.append(assignment)
        dropped = len(assignments) - len(kept)
        if dropped:
            logger.warning("live_assignments_without_shopify_order", dropped=dropped)
        return kept

    async def _list_assignments_live(
        self,
        *,
        scope: ScopeFilter,
        page_params: PageParams,
        unfulfilled_only: bool,
        call_status: str | None,
        follow_up_from: datetime | None,
        follow_up_to: datetime | None,
        date_from: datetime | None,
        date_to: datetime | None,
        q: str | None,
    ) -> tuple[list[OrderAssignment], int]:
        query = self.assignments.search_query(
            assigned_to=scope.assigned_to,
            team_leader_id=scope.team_leader_id,
            call_status=call_status,
            follow_up_from=follow_up_from,
            follow_up_to=follow_up_to,
        ).limit(LIVE_ASSIGNMENT_CAP)
        rows = list((await self.session.execute(query)).scalars().all())
        if len(rows) >= LIVE_ASSIGNMENT_CAP:
            logger.warning("live_assignment_cap_reached", cap=LIVE_ASSIGNMENT_CAP)
        attached = await self.attach_live_orders(rows)

        needle = q.lower() if q else None
        matched: list[OrderAssignment] = []
        for assignment in attached:
            order = assignment.order
            if unfulfilled_only and order.fulfillment_status != FulfillmentStatus.UNFULFILLED:
                continue
            if date_from is not None and order.order_datetime < date_from:
                continue
            if date_to is not None and order.order_datetime > date_to:
                continue
            if needle:
                haystack = " ".join(
                    filter(
                        None,
                        [
                            order.order_number,
                            order.customer.full_name if order.customer else None,
                            order.customer.phone if order.customer else None,
                        ],
                    )
                ).lower()
                if needle not in haystack:
                    continue
            matched.append(assignment)
        matched.sort(key=lambda a: a.order.order_datetime, reverse=True)
        start = (page_params.page - 1) * page_params.page_size
        return matched[start : start + page_params.page_size], len(matched)

    async def _pool_live(
        self,
        *,
        scope: ScopeFilter,
        page_params: PageParams,
        call_status: str | None,
        date_from: datetime | None,
        date_to: datetime | None,
        category: LeadCategory | None,
        unfulfilled_only: bool,
    ):
        """Live lead pool: Shopify orders (bounded crawl) that are either
        unassigned or already on the caller's team, paired with their
        active assignment. Same membership rules as the stored-data pools."""
        from sqlalchemy.orm import selectinload

        from app.services.lead_classification import classify_order
        from app.services.live_collect import collect_orders
        from app.services.order_ref import ref_id_for

        collected = await collect_orders(
            self.session,
            get_reader(),
            date_from=date_from,
            date_to=date_to,
            fulfillment_status=FulfillmentStatus.UNFULFILLED if unfulfilled_only else None,
            shopify_fulfillment_status="unfulfilled" if unfulfilled_only else None,
            max_pages=LIVE_POOL_PAGES,
        )
        self.last_pool_incomplete = collected.incomplete
        if collected.incomplete:
            logger.warning("live_pool_incomplete", pages=LIVE_POOL_PAGES)
        ref_ids = {
            o["external_id"]: ref_id_for(
                o["external_id"], collected.workflows.get(o["external_id"])
            )
            for o in collected.orders
        }
        assignments: dict = {}
        ids = list(ref_ids.values())
        for start in range(0, len(ids), 500):
            rows = (
                (
                    await self.session.execute(
                        select(OrderAssignment)
                        .where(
                            OrderAssignment.order_id.in_(ids[start : start + 500]),
                            OrderAssignment.assignment_status == AssignmentStatus.ACTIVE,
                        )
                        .options(selectinload(OrderAssignment.telecaller))
                    )
                )
                .scalars()
                .all()
            )
            assignments.update({a.order_id: a for a in rows})

        pairs = []
        for data in collected.orders:
            assignment = assignments.get(ref_ids[data["external_id"]])
            if (
                assignment is not None
                and scope.team_leader_id is not None
                and assignment.team_leader_id != scope.team_leader_id
            ):
                continue
            if call_status and (assignment is None or assignment.current_status != call_status):
                continue
            order = hydrate_order(data, collected.workflows.get(data["external_id"]))
            lead_category = classify_order(
                payment_type=order.payment_type, fulfillment_status=order.fulfillment_status
            )
            if lead_category is None:
                continue
            if category is not None and lead_category != category:
                continue
            pairs.append((order, assignment))
        start = (page_params.page - 1) * page_params.page_size
        return pairs[start : start + page_params.page_size], len(pairs)

    async def list_unfulfilled_pool(
        self,
        *,
        scope: ScopeFilter,
        page_params: PageParams,
        call_status: str | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
    ):
        if settings.SHOPIFY_LIVE_MODE:
            return await self._pool_live(
                scope=scope,
                page_params=page_params,
                call_status=call_status,
                date_from=date_from,
                date_to=date_to,
                category=None,
                unfulfilled_only=True,
            )
        return await self.assignments.list_unfulfilled_pool(
            team_leader_id=scope.team_leader_id,
            call_status=call_status,
            date_from=date_from,
            date_to=date_to,
            page_params=page_params,
        )

    async def list_lead_pool(
        self,
        *,
        scope: ScopeFilter,
        category: LeadCategory | None,
        page_params: PageParams,
        call_status: str | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
    ):
        """The widened Admin/Manager Lead Pool — COD Unfulfilled / COD
        Fulfilled / Prepaid orders, filterable by `category`. Leaves
        `list_unfulfilled_pool` above completely untouched (still backing
        the original all-unfulfilled-regardless-of-payment-type page).
        """
        if settings.SHOPIFY_LIVE_MODE:
            return await self._pool_live(
                scope=scope,
                page_params=page_params,
                call_status=call_status,
                date_from=date_from,
                date_to=date_to,
                category=category,
                unfulfilled_only=False,
            )
        return await self.assignments.list_category_pool(
            team_leader_id=scope.team_leader_id,
            category=category,
            call_status=call_status,
            date_from=date_from,
            date_to=date_to,
            page_params=page_params,
        )

    async def export_checkout_rows(
        self,
        *,
        scope: ScopeFilter,
        call_status: str | None,
        date_from: datetime | None,
        date_to: datetime | None,
    ) -> tuple[list, bool]:
        """Every checkout in the caller's pool as (checkout, assignment)
        pairs, plus whether the result is INCOMPLETE (live crawl bound hit)."""
        self.last_pool_incomplete = False
        if settings.SHOPIFY_LIVE_MODE:
            pairs, _ = await self._list_checkout_pool_live(
                scope=scope,
                page_params=PageParams.model_construct(page=1, page_size=1_000_000),
                call_status=call_status,
                date_from=date_from,
                date_to=date_to,
            )
            return pairs, self.last_pool_incomplete
        rows: list = []
        page = 1
        while True:
            pairs, total = await self.list_checkout_pool(
                scope=scope,
                page_params=PageParams(page=page, page_size=200),
                call_status=call_status,
                date_from=date_from,
                date_to=date_to,
            )
            rows.extend(pairs)
            if not pairs or len(rows) >= total:
                return rows, False
            page += 1

    async def attach_live_checkouts(
        self, assignments: list[CheckoutAssignment]
    ) -> list[CheckoutAssignment]:
        """Attaches each assignment's CURRENT checkout (fetched live, in
        batches) as an in-memory value. Assignments whose checkout Shopify
        no longer returns (and that have no local historical row) are
        omitted rather than shown from stale data."""
        if not assignments:
            return []
        workflows = {
            w.id: w
            for w in (
                await self.session.execute(
                    select(CheckoutWorkflow).where(
                        CheckoutWorkflow.id.in_({a.checkout_id for a in assignments})
                    )
                )
            )
            .scalars()
            .all()
        }
        external = [
            w.external_checkout_id
            for w in workflows.values()
            if not w.external_checkout_id.startswith(LOCAL_PREFIX)
        ]
        live = (
            {c["external_id"]: c for c in await fetch_checkouts_by_ids(external)}
            if external
            else {}
        )
        kept: list[CheckoutAssignment] = []
        for assignment in assignments:
            workflow = workflows.get(assignment.checkout_id)
            data = live.get(workflow.external_checkout_id) if workflow else None
            if data is not None:
                set_committed_value(assignment, "checkout", hydrate_checkout(data, workflow))
                kept.append(assignment)
            elif workflow is not None and workflow.external_checkout_id.startswith(LOCAL_PREFIX):
                local = await self.checkouts.get_by_id(assignment.checkout_id)
                if local is not None:
                    set_committed_value(assignment, "checkout", local)
                    kept.append(assignment)
        if len(kept) != len(assignments):
            logger.warning(
                "live_checkout_assignments_without_shopify_checkout",
                dropped=len(assignments) - len(kept),
            )
        return kept

    async def _list_checkout_assignments_live(
        self,
        *,
        scope: ScopeFilter,
        page_params: PageParams,
        call_status: str | None,
        follow_up_from: datetime | None,
        follow_up_to: datetime | None,
        date_from: datetime | None,
        date_to: datetime | None,
    ) -> tuple[list[CheckoutAssignment], int]:
        query = self.checkout_assignments.search_query(
            assigned_to=scope.assigned_to,
            team_leader_id=scope.team_leader_id,
            call_status=call_status,
            follow_up_from=follow_up_from,
            follow_up_to=follow_up_to,
        ).limit(LIVE_ASSIGNMENT_CAP)
        rows = list((await self.session.execute(query)).scalars().all())
        attached = await self.attach_live_checkouts(rows)
        kept = [
            a
            for a in attached
            if (date_from is None or (a.checkout.checkout_created_at or date_from) >= date_from)
            and (date_to is None or (a.checkout.checkout_created_at or date_to) <= date_to)
        ]
        kept.sort(key=lambda a: a.checkout.checkout_created_at or a.created_at, reverse=True)
        start = (page_params.page - 1) * page_params.page_size
        return kept[start : start + page_params.page_size], len(kept)

    async def _list_checkout_pool_live(
        self,
        *,
        scope: ScopeFilter,
        page_params: PageParams,
        call_status: str | None,
        date_from: datetime | None,
        date_to: datetime | None,
    ):
        from sqlalchemy.orm import selectinload

        from app.services.order_ref import checkout_ref_id_for

        checkouts, workflows, incomplete = await collect_checkouts(
            self.session, date_from=date_from, date_to=date_to, max_pages=LIVE_POOL_PAGES
        )
        self.last_pool_incomplete = incomplete
        if incomplete:
            logger.warning("live_checkout_pool_incomplete", pages=LIVE_POOL_PAGES)
        ref_ids = {
            c["external_id"]: checkout_ref_id_for(c["external_id"], workflows.get(c["external_id"]))
            for c in checkouts
        }
        assignments: dict = {}
        ids = list(ref_ids.values())
        for start in range(0, len(ids), 500):
            rows = (
                (
                    await self.session.execute(
                        select(CheckoutAssignment)
                        .where(
                            CheckoutAssignment.checkout_id.in_(ids[start : start + 500]),
                            CheckoutAssignment.assignment_status == AssignmentStatus.ACTIVE,
                        )
                        .options(selectinload(CheckoutAssignment.telecaller))
                    )
                )
                .scalars()
                .all()
            )
            assignments.update({a.checkout_id: a for a in rows})
        pairs = []
        for data in checkouts:
            assignment = assignments.get(ref_ids[data["external_id"]])
            if (
                assignment is not None
                and scope.team_leader_id is not None
                and assignment.team_leader_id != scope.team_leader_id
            ):
                continue
            if call_status and (assignment is None or assignment.current_status != call_status):
                continue
            pairs.append((hydrate_checkout(data, workflows.get(data["external_id"])), assignment))
        start = (page_params.page - 1) * page_params.page_size
        return pairs[start : start + page_params.page_size], len(pairs)

    async def _resolve_assignable_checkout_ids(
        self, checkout_ids: list[uuid.UUID]
    ) -> list[uuid.UUID]:
        """Live mode: validate each checkout (workflow row, live reference,
        or historical local row), verify live references against Shopify in
        one batch, refuse recovered ones, and ensure the workflow row."""
        resolved: dict[uuid.UUID, uuid.UUID] = {}
        to_verify: dict[str, uuid.UUID] = {}
        recovered_check: dict[str, uuid.UUID] = {}
        for checkout_id in checkout_ids:
            ref = await resolve_checkout_ref(self.session, checkout_id)
            if ref is not None:
                external, workflow = ref
                if workflow is not None:
                    resolved[checkout_id] = workflow.id
                    if not external.startswith(LOCAL_PREFIX):
                        recovered_check[external] = checkout_id
                else:
                    to_verify[external] = checkout_id
                    recovered_check[external] = checkout_id
            elif await self.checkouts.get_by_id(checkout_id) is not None:
                resolved[checkout_id] = checkout_id
            else:
                raise NotFoundError(f"Abandoned checkout {checkout_id} not found.")

        if recovered_check:
            live = {
                c["external_id"]: c for c in await fetch_checkouts_by_ids(list(recovered_check))
            }
            for external, checkout_id in recovered_check.items():
                data = live.get(external)
                if data is None:
                    raise NotFoundError(f"Abandoned checkout {checkout_id} not found in Shopify.")
                if data.get("is_recovered"):
                    raise ConflictError(
                        f"Checkout {checkout_id} has already been recovered (completed as an "
                        "order) and can no longer be assigned as a lead."
                    )
        for external, checkout_id in to_verify.items():
            resolved[checkout_id] = (await ensure_checkout_workflow(self.session, external)).id
        return [resolved[c] for c in checkout_ids]

    async def list_checkout_pool(
        self,
        *,
        scope: ScopeFilter,
        page_params: PageParams,
        call_status: str | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
    ):
        if settings.SHOPIFY_LIVE_MODE:
            return await self._list_checkout_pool_live(
                scope=scope,
                page_params=page_params,
                call_status=call_status,
                date_from=date_from,
                date_to=date_to,
            )
        return await self.checkout_assignments.list_pool(
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
        if (
            assignment is not None
            and settings.SHOPIFY_LIVE_MODE
            and not await self.attach_live_orders([assignment])
        ):
            assignment = None
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
        """The Telecaller order-detail page's Call History — the *current*
        state of every attempt (edits applied, deleted ones dropped), not
        the raw append-only row list. Full history (including void rows
        and every superseded edit) stays in the table for audit but is
        never surfaced to this read path.
        """
        await self.get_scoped_assignment(order_id, scope=scope)
        return await self.call_attempts.resolve_current_for_order(order_id)

    async def list_my_call_history(self, telecaller_id: uuid.UUID) -> list[tuple[CallAttempt, str]]:
        """Every call `telecaller_id` has personally made, across every
        order — always hard-scoped to the caller's own id by the endpoint
        layer, never a client-supplied telecaller id.
        """
        return await self.call_attempts.list_for_telecaller(telecaller_id)

    # ------------------------------------------------------------------
    # Checkout-lead listing / detail — `CheckoutAssignment`'s exact
    # counterparts of the order-assignment methods above.
    # ------------------------------------------------------------------

    async def list_checkout_assignments(
        self,
        *,
        scope: ScopeFilter,
        page_params: PageParams,
        sort_params: SortParams,
        call_status: str | None = None,
        when: str | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
    ) -> tuple[list[CheckoutAssignment], int]:
        follow_up_from, follow_up_to = _follow_up_window(when)
        if settings.SHOPIFY_LIVE_MODE:
            return await self._list_checkout_assignments_live(
                scope=scope,
                page_params=page_params,
                call_status=call_status,
                follow_up_from=follow_up_from,
                follow_up_to=follow_up_to,
                date_from=date_from,
                date_to=date_to,
            )
        query = self.checkout_assignments.search_query(
            assigned_to=scope.assigned_to,
            team_leader_id=scope.team_leader_id,
            call_status=call_status,
            follow_up_from=follow_up_from,
            follow_up_to=follow_up_to,
            date_from=date_from,
            date_to=date_to,
        )
        items, total = await self.checkout_assignments.list(
            page_params=page_params,
            sort_params=sort_params,
            query=query,
            default_sort_column="created_at",
        )
        return list(items), total

    async def get_scoped_checkout_assignment(
        self, checkout_id: uuid.UUID, *, scope: ScopeFilter
    ) -> CheckoutAssignment:
        assignment = await self.checkout_assignments.get_scoped(
            checkout_id, assigned_to=scope.assigned_to, team_leader_id=scope.team_leader_id
        )
        if (
            assignment is not None
            and settings.SHOPIFY_LIVE_MODE
            and not await self.attach_live_checkouts([assignment])
        ):
            assignment = None
        if assignment is None:
            raise AuthorizationError("Checkout not found or not assigned to you.")
        return assignment

    async def list_checkout_call_history(self, checkout_id: uuid.UUID, *, scope: ScopeFilter):
        await self.get_scoped_checkout_assignment(checkout_id, scope=scope)
        return await self.checkout_call_attempts.list_for_checkout(checkout_id)

    async def list_my_checkout_call_history(self, telecaller_id: uuid.UUID):
        return await self.checkout_call_attempts.list_for_telecaller(telecaller_id)

    async def team_summary(self, *, scope: ScopeFilter) -> dict[str, int | float]:
        """Combines order-lead and checkout-lead status counts into one
        set of numbers (spec: "Pending Calls"/"Completed Calls"/etc. are
        single dashboard tiles, not split by lead type) plus the
        per-category breakdown (Abandoned Checkouts / COD Unfulfilled /
        COD Fulfilled / Prepaid) and pool-wide total/unassigned counts —
        every number here is a real aggregate query, never a fabricated
        placeholder.
        """
        order_counts = await self.assignments.team_summary_counts(
            team_leader_id=scope.team_leader_id
        )
        checkout_counts = await self.checkout_assignments.team_summary_counts(
            team_leader_id=scope.team_leader_id
        )
        counts = _merge_counts(order_counts, checkout_counts)

        category_counts = await self.assignments.category_counts(
            team_leader_id=scope.team_leader_id
        )
        checkout_pool = await self.checkout_assignments.pool_counts(
            team_leader_id=scope.team_leader_id
        )

        total_leads = sum(c["total"] for c in category_counts.values()) + checkout_pool["total"]
        unassigned_leads = (
            sum(c["unassigned"] for c in category_counts.values()) + checkout_pool["unassigned"]
        )

        return {
            **_summary_from_counts(counts, await self._follow_ups_today_count(scope)),
            "total_leads": total_leads,
            "unassigned_leads": unassigned_leads,
            "abandoned_checkouts": checkout_pool["total"],
            "cod_unfulfilled": category_counts[LeadCategory.COD_UNFULFILLED.value]["total"],
            "cod_fulfilled": category_counts[LeadCategory.COD_FULFILLED.value]["total"],
            "prepaid": category_counts[LeadCategory.PREPAID.value]["total"],
        }

    async def telecaller_summary(self, *, scope: ScopeFilter) -> dict[str, int | float]:
        # `team_summary_counts` only supports team_leader-scoped grouping,
        # so a single telecaller's own summary re-derives counts here
        # instead — a dedicated single-telecaller aggregate query.
        order_counts = await self._status_counts_for_assigned_to(
            self.assignments, OrderAssignment, scope.assigned_to
        )
        checkout_counts = await self._status_counts_for_assigned_to(
            self.checkout_assignments, CheckoutAssignment, scope.assigned_to
        )
        counts = _merge_counts(order_counts, checkout_counts)
        category_counts = await self.assignments.category_counts(assigned_to=scope.assigned_to)
        return {
            **_summary_from_counts(counts, await self._follow_ups_today_count(scope)),
            "total_leads": sum(counts.values()),
            "unassigned_leads": 0,
            "abandoned_checkouts": sum(checkout_counts.values()),
            "cod_unfulfilled": category_counts[LeadCategory.COD_UNFULFILLED.value]["total"],
            "cod_fulfilled": category_counts[LeadCategory.COD_FULFILLED.value]["total"],
            "prepaid": category_counts[LeadCategory.PREPAID.value]["total"],
        }

    async def _status_counts_for_assigned_to(
        self, repository, model, assigned_to: uuid.UUID | None
    ) -> dict[str, int]:
        query = repository.search_query(assigned_to=assigned_to)
        rows = (
            (await self.session.execute(query.with_only_columns(model.current_status)))
            .scalars()
            .all()
        )
        counts: dict[str, int] = {}
        for status in rows:
            counts[status.value] = counts.get(status.value, 0) + 1
        return counts

    async def _follow_ups_today_count(self, scope: ScopeFilter) -> int:
        from sqlalchemy import func, select

        start, end = ist_day_bounds()
        order_query = self.assignments.search_query(
            assigned_to=scope.assigned_to,
            team_leader_id=scope.team_leader_id,
            follow_up_from=start,
            follow_up_to=end,
        )
        checkout_query = self.checkout_assignments.search_query(
            assigned_to=scope.assigned_to,
            team_leader_id=scope.team_leader_id,
            follow_up_from=start,
            follow_up_to=end,
        )
        order_total = await self.session.scalar(
            select(func.count()).select_from(order_query.subquery())
        )
        checkout_total = await self.session.scalar(
            select(func.count()).select_from(checkout_query.subquery())
        )
        return (order_total or 0) + (checkout_total or 0)

    async def team_telecaller_performance(self, *, scope: ScopeFilter) -> list[dict]:
        order_breakdown = dict(
            await self.assignments.telecaller_performance(team_leader_id=scope.team_leader_id)
        )
        checkout_breakdown = dict(
            await self.checkout_assignments.telecaller_performance(
                team_leader_id=scope.team_leader_id
            )
        )
        telecaller_ids = set(order_breakdown) | set(checkout_breakdown)
        # Unscoped by team_leader_id (that's an `OrderAssignment` concept,
        # not something `Order.confirmed_by_telecaller_id` can filter on
        # directly) — `telecaller_ids` above is already correctly
        # team-scoped from the assignment breakdowns, so looking each one
        # up in this single unscoped query is both correct and one round
        # trip instead of N.
        confirmation_counts = await self.orders.telecaller_confirmation_counts()

        results = []
        for telecaller_id in telecaller_ids:
            telecaller = await self.users.get_by_id(telecaller_id)
            merged = _merge_counts(
                order_breakdown.get(telecaller_id, {}), checkout_breakdown.get(telecaller_id, {})
            )
            confirmation = confirmation_counts.get(
                telecaller_id, {"confirmed": 0, "shipped": 0, "delivered": 0, "ndr": 0, "rto": 0}
            )
            results.append(
                {
                    "telecaller_id": telecaller_id,
                    "telecaller_name": telecaller.name if telecaller else "Unknown",
                    "orders_confirmed": confirmation["confirmed"],
                    "shipped": confirmation["shipped"],
                    "delivered": confirmation["delivered"],
                    **_performance_from_counts(merged),
                }
            )
        return results

    async def telecaller_detail_summary(
        self, telecaller_id: uuid.UUID, *, actor: User
    ) -> dict[str, object]:
        """Summary cards for the individual Telecaller dashboard (Team
        Leader/Admin view of one telecaller's own numbers) — real
        aggregate queries, same as `team_telecaller_performance`, plus
        `fulfilled`: a *live* count of this telecaller's currently
        assigned orders that are currently `FULFILLED`, not a historical
        "fulfilled on day X" figure. No `fulfilled_at`/equivalent
        timestamp exists anywhere in this schema (`Order`, `Shipment`, and
        the append-only `OrderEvent` order timeline were all checked —
        fulfillment sync overwrites `Order.fulfillment_status` directly
        without logging a dedicated event), so a day-bucketed fulfillment
        figure is deliberately not offered here or in the daily graph
        rather than approximated from an unrelated timestamp like
        `Order.updated_at`.
        """
        telecaller = await self.assert_telecaller_in_team_scope(telecaller_id, actor=actor)
        order_breakdown = dict(
            await self.assignments.telecaller_performance(
                team_leader_id=None, telecaller_id=telecaller_id
            )
        )
        checkout_breakdown = dict(
            await self.checkout_assignments.telecaller_performance(
                team_leader_id=None, telecaller_id=telecaller_id
            )
        )
        merged = _merge_counts(
            order_breakdown.get(telecaller_id, {}), checkout_breakdown.get(telecaller_id, {})
        )
        fulfilled_by_telecaller = await self.assignments.fulfilled_counts(
            telecaller_id=telecaller_id
        )
        total_attempts, orders_with_attempts = await self.assignments.attempt_averaging_stats(
            telecaller_id=telecaller_id
        )
        # "Average Call Attempts" -- total attempts / unique orders that
        # have been called at least once, never divided by every assigned
        # order (an untouched order isn't a "0-attempt call," it just
        # hasn't been reached yet, and would otherwise drag the average
        # down for no operationally meaningful reason). 0.0 when nothing
        # has been called yet, not a division error.
        average_call_attempts = (
            round(total_attempts / orders_with_attempts, 1) if orders_with_attempts else 0.0
        )
        confirmation = (
            await self.orders.telecaller_confirmation_counts(telecaller_id=telecaller_id)
        ).get(telecaller_id, {"confirmed": 0, "shipped": 0, "delivered": 0, "ndr": 0, "rto": 0})
        return {
            "telecaller_id": telecaller_id,
            "telecaller_name": telecaller.name,
            "cancelled": merged.get(TelecallingStatus.CANCELLED.value, 0),
            "fulfilled": fulfilled_by_telecaller.get(telecaller_id, 0),
            "total_attempts": total_attempts,
            "average_call_attempts": average_call_attempts,
            "orders_confirmed": confirmation["confirmed"],
            "shipped": confirmation["shipped"],
            "delivered": confirmation["delivered"],
            "ndr": confirmation["ndr"],
            "rto": confirmation["rto"],
            **_performance_from_counts(merged),
        }

    async def telecaller_daily_performance(
        self, telecaller_id: uuid.UUID, *, actor: User, date_from: datetime, date_to: datetime
    ) -> list[dict[str, object]]:
        """Day-bucketed (IST calendar day) call-attempt activity for the
        daily performance graph — built from real `CallAttempt` rows in
        range, resolved through any edits/deletes (see
        `CallAttemptRepository.list_resolved_for_telecaller_in_range`), so
        a deleted attempt never inflates a day's numbers and an edited
        attempt is counted by its corrected outcome. "Follow-ups" here
        means calls that day which set a `next_follow_up_at`, not the
        dashboard's current-snapshot "assignments with a pending
        follow-up" figure — a different, both-valid meaning of the same
        word for a per-day series.
        """
        await self.assert_telecaller_in_team_scope(telecaller_id, actor=actor)
        attempts = await self.call_attempts.list_resolved_for_telecaller_in_range(
            telecaller_id, date_from=date_from, date_to=date_to
        )

        buckets: dict[str, dict[str, int]] = {}
        for attempt in attempts:
            key = to_ist(attempt.attempted_at).date().isoformat()
            bucket = buckets.setdefault(
                key,
                {
                    "attempts": 0,
                    "connected": 0,
                    "confirmed": 0,
                    "not_interested": 0,
                    "cancelled": 0,
                    "follow_ups": 0,
                },
            )
            bucket["attempts"] += 1
            # "Connected" is a computed superset (see `_NOT_CONNECTED_
            # OUTCOMES`'s docstring) that overlaps confirmed/not_interested/
            # cancelled -- each of these is checked independently, never as
            # an elif chain, so a CONFIRMED attempt counts in *both* its own
            # bucket and the broader "connected" one.
            if attempt.outcome not in _NOT_CONNECTED_OUTCOMES:
                bucket["connected"] += 1
            if attempt.outcome == TelecallingStatus.CONFIRMED:
                bucket["confirmed"] += 1
            elif attempt.outcome == TelecallingStatus.NOT_INTERESTED:
                bucket["not_interested"] += 1
            elif attempt.outcome == TelecallingStatus.CANCELLED:
                bucket["cancelled"] += 1
            if attempt.next_follow_up_at is not None:
                bucket["follow_ups"] += 1

        return [{"date": date, **counts} for date, counts in sorted(buckets.items())]

    async def list_assignable_telecallers(self, *, scope: ScopeFilter) -> list[User]:
        """The roster for a "Select Telecaller" assignment dropdown — every
        active TELECALLER-role user in scope, regardless of whether they
        already have any lead assigned (unlike `team_telecaller_performance`
        above, which only ever surfaces telecallers with existing assignment
        activity and is the wrong data source for "who can I assign to").
        Reuses `UserRepository`/the same `resolve_team_scope` convention as
        every other `/team/*` read — no second Telecaller list.
        """
        return await self.users.list_by_role("TELECALLER", team_leader_id=scope.team_leader_id)

    async def assert_telecaller_in_team_scope(
        self, telecaller_id: uuid.UUID, *, actor: User
    ) -> User:
        telecaller = await self.users.get_with_permissions(telecaller_id)
        if telecaller is None or "TELECALLER" not in telecaller.role_names:
            raise NotFoundError("Telecaller not found.")
        # An ADMIN (role-based, same as `resolve_team_scope`) has global
        # assignment scope, same as a superuser -- a real production Admin
        # account (`is_superuser=False`) was blocked from assigning to an
        # active TELECALLER whose `team_leader_id` didn't happen to equal
        # the Admin's own id (e.g. `None`, this OMS's only team/telecaller
        # having no team leader set), even though the roster already
        # correctly listed that telecaller as assignable.
        is_global_scope = actor.is_superuser or "ADMIN" in actor.role_names
        if not is_global_scope and telecaller.team_leader_id != actor.id:
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
        order_ids = await self._resolve_assignable_order_ids(order_ids)
        conflicts: list[str] = []
        for order_id in order_ids:
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

    async def _resolve_assignable_order_ids(self, order_ids: list[uuid.UUID]) -> list[uuid.UUID]:
        """Validates every order exists and returns the reference id to
        store on each assignment.

        Legacy mode: the order must be a local `orders` row (unchanged).
        Live mode (`SHOPIFY_LIVE_MODE`): an id may be an `order_workflow`
        row, a reversible live-order reference, or (for historical
        non-Shopify rows) a local order. Live references are verified
        against Shopify in ONE batched call, then an `order_workflow` row
        (external id + default OMS state, no Shopify fields) is ensured
        -- the only thing persisted for the order.
        """
        if not settings.SHOPIFY_LIVE_MODE:
            for order_id in order_ids:
                if await self.orders.get_by_id(order_id) is None:
                    raise NotFoundError(f"Order {order_id} not found.")
            return order_ids

        resolved: dict[uuid.UUID, uuid.UUID] = {}
        to_verify: dict[str, uuid.UUID] = {}
        for order_id in order_ids:
            ref = await resolve_ref(self.session, order_id)
            if ref is not None and ref.workflow is not None:
                resolved[order_id] = ref.workflow.id
            elif ref is not None:
                to_verify[ref.external_order_id] = order_id
            elif await self.orders.get_by_id(order_id) is not None:
                resolved[order_id] = order_id
            else:
                raise NotFoundError(f"Order {order_id} not found.")

        if to_verify:
            reader = require_live_reader(get_adapter(IntegrationCode.SHOPIFY))
            live = await reader.get_orders_by_ids(list(to_verify))
            present = {o["external_id"] for o in live}
            for external_id, order_id in to_verify.items():
                if external_id not in present:
                    raise NotFoundError(f"Order {order_id} not found in Shopify.")
                resolved[order_id] = (await ensure_workflow(self.session, external_id)).id
        return [resolved[order_id] for order_id in order_ids]

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
    # Checkout assignment mutations — `OrderAssignment`'s exact
    # counterparts (`assign_orders`/`reassign_order` above), operating on
    # `AbandonedCheckout`/`CheckoutAssignment` instead.
    # ------------------------------------------------------------------

    async def assign_checkouts(
        self,
        *,
        checkout_ids: list[uuid.UUID],
        mode: str,
        telecaller_id: uuid.UUID | None,
        telecaller_ids: list[uuid.UUID] | None,
        actor: User,
    ) -> list[CheckoutAssignment]:
        if mode == "manual":
            if telecaller_id is None:
                raise ValidationError("telecaller_id is required for manual assignment.")
            candidate_ids: list[uuid.UUID] = [telecaller_id]
        else:
            candidate_ids = telecaller_ids or []
        telecallers = await self._resolve_and_validate_telecallers(candidate_ids, actor=actor)

        if settings.SHOPIFY_LIVE_MODE:
            checkout_ids = await self._resolve_assignable_checkout_ids(checkout_ids)
        conflicts: list[str] = []
        for checkout_id in checkout_ids:
            if settings.SHOPIFY_LIVE_MODE:
                if await self.checkout_assignments.get_active_for_checkout(checkout_id) is not None:
                    conflicts.append(str(checkout_id))
                continue
            checkout = await self.checkouts.get_by_id(checkout_id)
            if checkout is None:
                raise NotFoundError(f"Abandoned checkout {checkout_id} not found.")
            if checkout.is_recovered:
                raise ConflictError(
                    f"Checkout {checkout_id} has already been recovered (completed as an order) "
                    "and can no longer be assigned as a lead."
                )
            if await self.checkout_assignments.get_active_for_checkout(checkout_id) is not None:
                conflicts.append(str(checkout_id))
        if conflicts:
            raise ConflictError(
                "The following checkouts already have an active assignment — "
                "use reassign instead: " + ", ".join(conflicts),
                details={"checkout_ids": conflicts},
            )

        now = datetime.now(UTC)
        created: list[CheckoutAssignment] = []
        for index, checkout_id in enumerate(checkout_ids):
            telecaller = telecallers[index % len(telecallers)]
            assignment = await self.checkout_assignments.create(
                checkout_id=checkout_id,
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
                action="checkout.assigned",
                entity_type="abandoned_checkout",
                entity_id=str(checkout_id),
                new_value={"assigned_to": str(telecaller.id), "mode": mode},
            )

        await self.session.commit()
        return created

    async def reassign_checkout(
        self, *, checkout_id: uuid.UUID, new_telecaller_id: uuid.UUID, reason: str, actor: User
    ) -> CheckoutAssignment:
        current = await self.checkout_assignments.get_active_for_checkout(checkout_id)
        if current is None:
            raise NotFoundError(
                "Checkout has no active assignment to reassign — use assign instead."
            )

        (new_telecaller,) = await self._resolve_and_validate_telecallers(
            [new_telecaller_id], actor=actor
        )
        if not actor.is_superuser and current.team_leader_id != actor.id:
            raise AuthorizationError("That checkout is not on your team.")

        previous_telecaller_id = current.assigned_to
        now = datetime.now(UTC)

        await self.checkout_assignments.update(current, assignment_status=AssignmentStatus.INACTIVE)

        new_assignment = await self.checkout_assignments.create(
            checkout_id=checkout_id,
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
            action="checkout.reassigned",
            entity_type="abandoned_checkout",
            entity_id=str(checkout_id),
            previous_value={"telecaller_id": str(previous_telecaller_id)},
            new_value={"telecaller_id": str(new_telecaller.id), "reason": reason},
        )
        await self.session.commit()
        return new_assignment

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
        """Logs a call attempt. When `outcome == TelecallingStatus.
        CONFIRMED`, this ALSO confirms the OMS order itself (review-meeting
        Requirement 1) -- selecting "Confirmed" as the call outcome in the
        Log Call modal is the primary, expected way a Telecaller confirms
        an order; the dedicated `POST /telecaller/orders/{id}/confirm`
        endpoint (`confirm_assigned_order`, below) is an alternate/explicit
        entry point to the exact same canonical `OrderService.confirm_order`
        -- never a second, competing implementation. See
        `_confirm_order_from_call_log`'s docstring for why this is
        deliberately best-effort/idempotent rather than failing the call
        log itself.
        """
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
        assignment_updates: dict[str, object] = {
            "current_status": outcome,
            "attempt_count": assignment.attempt_count + 1,
            "last_attempt_at": now,
        }
        # `next_follow_up_at=None` means "this call didn't set one" (e.g.
        # every quick-log button — Mark Confirmed/Not Interested/Cancelled
        # — sends only `outcome`), not "clear whatever follow-up is
        # already scheduled". Only overwrite it when this call actually
        # provided a new value -- otherwise a follow-up date set on an
        # earlier call was silently wiped by any later call that didn't
        # re-specify one (confirmed: this was happening every time).
        if next_follow_up_at is not None:
            assignment_updates["next_follow_up_at"] = next_follow_up_at
        await self.assignments.update(assignment, **assignment_updates)
        await self.audit.record(
            user=actor,
            action="call.logged",
            entity_type="order",
            entity_id=str(order_id),
            new_value={"outcome": outcome.value, "attempt_number": attempt_number},
        )
        # The call log is committed on its own here, BEFORE the order-
        # confirmation attempt below — logging a call must never fail (or
        # be left half-saved) merely because the order happens to already
        # be confirmed/cancelled/otherwise unable to transition; see
        # `_confirm_order_from_call_log`.
        await self.session.commit()

        if outcome == TelecallingStatus.CONFIRMED:
            await self._confirm_order_from_call_log(order_id, actor=actor)

        # Review-meeting Requirements 1-3: sync the order's current call-
        # outcome tag + permanent channel tag to Shopify. Runs for EVERY
        # logged outcome (including CONFIRMED, to clear a stale prior-
        # outcome tag and refresh the channel tag) -- best-effort, after
        # the commit above, same placement/failure-isolation contract as
        # `_confirm_order_from_call_log`. See `ShopifyFulfillmentService.
        # sync_call_tags`.
        from app.services.shopify_fulfillment_service import ShopifyFulfillmentService

        await ShopifyFulfillmentService(self.session).sync_call_tags(
            order_id, outcome=outcome, actor=actor
        )

        return attempt

    async def _confirm_order_from_call_log(self, order_id: uuid.UUID, *, actor: User) -> None:
        """Delegates to the exact same canonical `OrderService.
        confirm_order` the direct confirm endpoint uses (same PENDING ->
        CONFIRMED transition, same `confirmed_by_telecaller_id`/
        `confirmed_at` attribution from this call's own authenticated
        `actor` — never a client-supplied id, same best-effort Shopify
        "OMS Confirmed" + per-telecaller tag push) -- never a second,
        divergent confirmation implementation.

        Best-effort and idempotent from the call-log's point of view: the
        call log above has ALREADY been saved and committed regardless of
        what happens here. If the order isn't currently in a state that
        can transition to CONFIRMED -- already CONFIRMED (a telecaller
        logging a second "Confirmed" outcome on an already-confirmed
        order, the exact scenario Requirement 11 calls out), or CANCELLED,
        or already further along (PROCESSING/PACKED/...) -- `transition_
        status` raises `ConflictError` before writing anything, which is
        swallowed here rather than failing the whole Log Call request:
        logging a call outcome must never itself return an error merely
        because of the order's current lifecycle state. Any OTHER
        exception (a genuine bug, not an expected state conflict) is
        deliberately NOT swallowed — it still surfaces normally.
        """
        try:
            await self.order_service.confirm_order(order_id, actor=actor)
        except ConflictError:
            logger.info(
                "call_log_confirmed_outcome_order_already_settled",
                order_id=str(order_id),
            )

    async def edit_call_attempt(
        self,
        order_id: uuid.UUID,
        attempt_id: uuid.UUID,
        *,
        outcome: TelecallingStatus,
        notes: str | None,
        next_follow_up_at: datetime | None,
        actor: User,
    ) -> CallAttempt:
        """Corrects an existing call attempt's outcome/notes/follow-up —
        implemented as a new row that `corrects_attempt_id`s the current
        one, never a mutation (`CallAttempt` stays append-only; see its
        model docstring). Scoped identically to `log_call`: only the
        telecaller the order is actively assigned to (or an admin) may
        edit, and only an order that's actually theirs.
        """
        assignment = await self.assignments.get_active_for_order(order_id)
        if assignment is None:
            raise NotFoundError("Order is not currently assigned.")
        if not actor.is_superuser and assignment.assigned_to != actor.id:
            raise AuthorizationError("This order is not assigned to you.")

        target = await self.call_attempts.get_correctable_for_order(attempt_id, order_id)
        if target is None:
            raise NotFoundError("Call attempt not found.")

        correction = await self.call_attempts.create(
            order_id=order_id,
            telecaller_id=actor.id,
            attempt_number=target.attempt_number,
            attempted_at=target.attempted_at,
            outcome=outcome,
            notes=notes,
            next_follow_up_at=next_follow_up_at,
            corrects_attempt_id=target.id,
            is_void=False,
        )
        await self._recompute_assignment_from_attempts(
            order_id, assignment=assignment, edited_or_deleted=target
        )
        await self.audit.record(
            user=actor,
            action="call_attempt.edited",
            entity_type="call_attempt",
            entity_id=str(target.id),
            previous_value={"outcome": target.outcome.value, "notes": target.notes},
            new_value={"outcome": outcome.value, "notes": notes},
        )
        await self.session.commit()
        return correction

    async def delete_call_attempt(
        self, order_id: uuid.UUID, attempt_id: uuid.UUID, *, actor: User
    ) -> None:
        """Removes a call attempt from the visible call history — again a
        new (void) correction row, never a real delete, so the full trail
        survives for audit even though the UI shows it as gone. Same
        ownership scoping as `edit_call_attempt`/`log_call`.
        """
        assignment = await self.assignments.get_active_for_order(order_id)
        if assignment is None:
            raise NotFoundError("Order is not currently assigned.")
        if not actor.is_superuser and assignment.assigned_to != actor.id:
            raise AuthorizationError("This order is not assigned to you.")

        target = await self.call_attempts.get_correctable_for_order(attempt_id, order_id)
        if target is None:
            raise NotFoundError("Call attempt not found.")

        await self.call_attempts.create(
            order_id=order_id,
            telecaller_id=actor.id,
            attempt_number=target.attempt_number,
            attempted_at=target.attempted_at,
            outcome=target.outcome,
            notes=target.notes,
            next_follow_up_at=target.next_follow_up_at,
            corrects_attempt_id=target.id,
            is_void=True,
        )
        await self._recompute_assignment_from_attempts(
            order_id, assignment=assignment, edited_or_deleted=target
        )
        await self.audit.record(
            user=actor,
            action="call_attempt.deleted",
            entity_type="call_attempt",
            entity_id=str(target.id),
            previous_value={
                "outcome": target.outcome.value,
                "attempt_number": target.attempt_number,
            },
            new_value=None,
        )
        await self.session.commit()

    async def _recompute_assignment_from_attempts(
        self, order_id: uuid.UUID, *, assignment: OrderAssignment, edited_or_deleted: CallAttempt
    ) -> None:
        """Re-derives `attempt_count`/`current_status`/`last_attempt_at`
        from the *current* resolved attempt list — never incremented/
        decremented, since an edit or delete can change any attempt, not
        just the latest one. `next_follow_up_at` is left alone unless
        `edited_or_deleted` was the most-recently-attempted active call
        (the only case where it's plausible that attempt is what's
        currently driving the assignment's follow-up date) — it can also
        be set independently via `schedule_follow_up`, with no record of
        which call (if any) actually set it, so this is deliberately
        narrow rather than guessing.
        """
        resolved = await self.call_attempts.resolve_current_for_order(order_id)
        was_most_recent = not resolved or all(
            r.attempted_at <= edited_or_deleted.attempted_at
            for r in resolved
            if r.attempt_number != edited_or_deleted.attempt_number
        )

        updates: dict[str, object] = {"attempt_count": len(resolved)}
        if resolved:
            latest = max(resolved, key=lambda r: (r.attempted_at, r.attempt_number))
            updates["current_status"] = latest.outcome
            updates["last_attempt_at"] = latest.attempted_at
            if was_most_recent:
                updates["next_follow_up_at"] = latest.next_follow_up_at
        else:
            updates["current_status"] = TelecallingStatus.NOT_CALLED
            updates["last_attempt_at"] = None
            if was_most_recent:
                updates["next_follow_up_at"] = None
        await self.assignments.update(assignment, **updates)

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
        if settings.SHOPIFY_LIVE_MODE:
            await self.attach_live_orders([assignment])
        return assignment

    async def confirm_assigned_order(self, order_id: uuid.UUID, *, actor: User) -> Order:
        """Telecaller-scoped order confirmation (PENDING -> CONFIRMED
        only — a distinct concept from `TelecallingStatus`/
        `FulfillmentStatus`/`ShipmentStatus`, never conflated with any of
        them). Same ownership check as `log_call`/`schedule_follow_up`
        (`assigned_to == actor.id` unless superuser), then delegates the
        actual transition to `OrderService.confirm_order` — no
        transition-rule or attribution logic duplicated here.
        """
        assignment = await self.assignments.get_active_for_order(order_id)
        if assignment is None:
            raise NotFoundError("Order is not currently assigned.")
        if not actor.is_superuser and assignment.assigned_to != actor.id:
            raise AuthorizationError("This order is not assigned to you.")
        return await self.order_service.confirm_order(order_id, actor=actor)

    async def bulk_confirm_assigned_orders(
        self, order_ids: list[uuid.UUID], *, actor: User
    ) -> list[dict[str, object]]:
        """Confirms each order independently — one order failing (already
        confirmed, not assigned to this telecaller, unknown id) never
        blocks or rolls back the others, per-order results are returned
        instead of raising, and each order's confirm already commits on
        its own success. An unexpected (non-`OMSError`) failure rolls the
        session back before continuing so it can't corrupt the next
        order's attempt (`confirm_order`/`transition_status` commit
        per-order, but a mid-flush error would otherwise leave the
        session needing a rollback before any further query succeeds).
        """
        results: list[dict[str, object]] = []
        for order_id in order_ids:
            try:
                await self.confirm_assigned_order(order_id, actor=actor)
            except (NotFoundError, AuthorizationError, ConflictError, ValidationError) as exc:
                results.append({"order_id": order_id, "success": False, "message": exc.message})
            except Exception:
                await self.session.rollback()
                results.append(
                    {
                        "order_id": order_id,
                        "success": False,
                        "message": "Could not confirm this order.",
                    }
                )
            else:
                results.append({"order_id": order_id, "success": True, "message": None})
        return results

    async def unconfirm_assigned_order(self, order_id: uuid.UUID, *, actor: User) -> Order:
        """The exact inverse of `confirm_assigned_order` — reverts a
        mistaken confirmation back to PENDING. Same ownership check
        (`assigned_to == actor.id` unless superuser); the actual revert +
        attribution cleanup is `OrderService.unconfirm_order`'s job.
        """
        assignment = await self.assignments.get_active_for_order(order_id)
        if assignment is None:
            raise NotFoundError("Order is not currently assigned.")
        if not actor.is_superuser and assignment.assigned_to != actor.id:
            raise AuthorizationError("This order is not assigned to you.")
        return await self.order_service.unconfirm_order(order_id, actor=actor)

    async def update_assigned_order_address(
        self, order_id: uuid.UUID, *, actor: User, address: dict
    ) -> Order:
        """Telecaller-scoped shipping-address edit. Same ownership check
        as `confirm_assigned_order`/`unconfirm_assigned_order`
        (`assigned_to == actor.id` unless superuser) -- a Telecaller can
        only edit the address of an order actually assigned to them, and
        never gains any broader order-edit access through this method.
        The actual OMS write + Shopify sync is `OrderService.
        update_shipping_address`'s job.
        """
        assignment = await self.assignments.get_active_for_order(order_id)
        if assignment is None:
            raise NotFoundError("Order is not currently assigned.")
        if not actor.is_superuser and assignment.assigned_to != actor.id:
            raise AuthorizationError("This order is not assigned to you.")
        return await self.order_service.update_shipping_address(
            order_id, actor=actor, address=address
        )

    async def update_assigned_order_item_variant(
        self,
        order_id: uuid.UUID,
        item_id: uuid.UUID,
        *,
        actor: User,
        new_variant_id: uuid.UUID,
    ) -> Order:
        """Telecaller-scoped product flavour/variant edit. Same ownership
        check as `update_assigned_order_address` (`assigned_to == actor.id`
        unless superuser) -- a Telecaller can only change the flavour of an
        item on an order actually assigned to them. The actual OMS write
        + audit/history recording is `OrderService.update_item_variant`'s
        job.
        """
        assignment = await self.assignments.get_active_for_order(order_id)
        if assignment is None:
            raise NotFoundError("Order is not currently assigned.")
        if not actor.is_superuser and assignment.assigned_to != actor.id:
            raise AuthorizationError("This order is not assigned to you.")
        return await self.order_service.update_item_variant(
            order_id, item_id, actor=actor, new_variant_id=new_variant_id
        )

    async def list_available_variants_for_item(
        self, order_id: uuid.UUID, item_id: uuid.UUID, *, actor: User
    ) -> list[ProductVariant]:
        """Options for the "change flavour" dropdown -- every variant of
        the item's own product (`ProductVariantRepository.list_for_product`
        via the already-wired `OrderService.variants`), never a second
        product-catalog read path. Same ownership check as every other
        `/telecaller/orders/{id}/*` method.
        """
        assignment = await self.assignments.get_active_for_order(order_id)
        if assignment is None:
            raise NotFoundError("Order is not currently assigned.")
        if not actor.is_superuser and assignment.assigned_to != actor.id:
            raise AuthorizationError("This order is not assigned to you.")

        item = await self.order_service.order_items.get_by_id(item_id)
        if item is None or item.order_id != order_id:
            raise NotFoundError("Order item not found.")
        if item.product_variant_id is None:
            return []
        current_variant = await self.order_service.variants.get_by_id(item.product_variant_id)
        if current_variant is None:
            return []
        return await self.order_service.variants.list_for_product(current_variant.product_id)

    async def get_activity_for_assigned_order(
        self, order_id: uuid.UUID, *, actor: User
    ) -> list[OrderEvent]:
        """The order-detail page's "Activity History" panel -- the append-
        only `OrderEvent` timeline for this order (address/flavour changes,
        status transitions, ...), newest first. Same ownership check as
        every other `/telecaller/orders/{id}/*` method (`assigned_to ==
        actor.id` unless superuser).
        """
        assignment = await self.assignments.get_active_for_order(order_id)
        if assignment is None:
            raise NotFoundError("Order is not currently assigned.")
        if not actor.is_superuser and assignment.assigned_to != actor.id:
            raise AuthorizationError("This order is not assigned to you.")
        events = await self.order_events.list_for_order(order_id)
        return list(reversed(events))

    async def get_previous_orders_for_assigned_order(
        self, order_id: uuid.UUID, *, actor: User, limit: int = 20
    ) -> list[Order]:
        """The order-detail page's "Customer's previous orders" panel.
        Same ownership check as every other `/telecaller/orders/{id}/*`
        method (`assigned_to == actor.id` unless superuser) — a Telecaller
        can only see order history for a customer via an order actually
        assigned to them, never by guessing a customer id directly (there
        is no customer-id-keyed route here at all).

        Returns `[]` (never 404/403) for an order with no linked
        `Customer` (a guest/manual order) — "no previous orders" is a
        normal, expected state, not an error. The current order itself is
        always excluded from its own history.
        """
        assignment = await self.assignments.get_active_for_order(order_id)
        if assignment is None:
            raise NotFoundError("Order is not currently assigned.")
        if not actor.is_superuser and assignment.assigned_to != actor.id:
            raise AuthorizationError("This order is not assigned to you.")

        order = await self.orders.get_by_id(order_id)
        if order is None or order.customer_id is None:
            return []
        return await self.orders.previous_orders_for_customer(
            order.customer_id, exclude_order_id=order_id, limit=limit
        )

    # ------------------------------------------------------------------
    # Checkout calling / follow-up mutations — `log_call`/
    # `schedule_follow_up`'s exact counterparts.
    # ------------------------------------------------------------------

    async def log_checkout_call(
        self,
        checkout_id: uuid.UUID,
        *,
        outcome: TelecallingStatus,
        notes: str | None,
        next_follow_up_at: datetime | None,
        actor: User,
    ):
        assignment = await self.checkout_assignments.get_active_for_checkout(checkout_id)
        if assignment is None:
            raise NotFoundError("Checkout is not currently assigned.")
        if not actor.is_superuser and assignment.assigned_to != actor.id:
            raise AuthorizationError("This checkout is not assigned to you.")

        attempt_number = await self.checkout_call_attempts.next_attempt_number(checkout_id)
        now = datetime.now(UTC)
        attempt = await self.checkout_call_attempts.create(
            checkout_id=checkout_id,
            telecaller_id=actor.id,
            attempt_number=attempt_number,
            attempted_at=now,
            outcome=outcome,
            notes=notes,
            next_follow_up_at=next_follow_up_at,
        )
        assignment_updates: dict[str, object] = {
            "current_status": outcome,
            "attempt_count": assignment.attempt_count + 1,
            "last_attempt_at": now,
        }
        # See `log_call`'s identical comment: `None` means "this call
        # didn't set one", not "clear the existing follow-up".
        if next_follow_up_at is not None:
            assignment_updates["next_follow_up_at"] = next_follow_up_at
        await self.checkout_assignments.update(assignment, **assignment_updates)
        await self.audit.record(
            user=actor,
            action="checkout_call.logged",
            entity_type="abandoned_checkout",
            entity_id=str(checkout_id),
            new_value={"outcome": outcome.value, "attempt_number": attempt_number},
        )
        await self.session.commit()
        return attempt

    async def schedule_checkout_follow_up(
        self, checkout_id: uuid.UUID, *, next_follow_up_at: datetime, actor: User
    ) -> CheckoutAssignment:
        assignment = await self.checkout_assignments.get_active_for_checkout(checkout_id)
        if assignment is None:
            raise NotFoundError("Checkout is not currently assigned.")
        if not actor.is_superuser and assignment.assigned_to != actor.id:
            raise AuthorizationError("This checkout is not assigned to you.")

        previous = assignment.next_follow_up_at
        await self.checkout_assignments.update(assignment, next_follow_up_at=next_follow_up_at)
        await self.audit.record(
            user=actor,
            action="checkout_followup.scheduled",
            entity_type="abandoned_checkout",
            entity_id=str(checkout_id),
            previous_value={"next_follow_up_at": previous.isoformat() if previous else None},
            new_value={"next_follow_up_at": next_follow_up_at.isoformat()},
        )
        await self.session.commit()
        if settings.SHOPIFY_LIVE_MODE:
            await self.attach_live_checkouts([assignment])
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


def _merge_counts(*count_dicts: dict[str, int]) -> dict[str, int]:
    """Sums two status-count dicts key-by-key — order-lead and
    checkout-lead breakdowns share the exact same `TelecallingStatus`
    vocabulary (plus the `"_follow_ups"` pseudo-status), so a combined
    dashboard/performance number is just an elementwise sum, never a
    second aggregate query.
    """
    merged: dict[str, int] = {}
    for counts in count_dicts:
        for key, value in counts.items():
            merged[key] = merged.get(key, 0) + value
    return merged


def _performance_from_counts(counts: dict[str, int]) -> dict[str, int | float]:
    """Shapes one telecaller's status-count breakdown (from
    `OrderAssignmentRepository.telecaller_performance`, including its
    `"_follow_ups"` pseudo-status) into `TelecallerPerformanceResponse`'s
    fields for the Team Leader's performance table.
    """
    counts = dict(counts)
    follow_ups = counts.pop("_follow_ups", 0)
    assigned = sum(counts.values())
    not_called = counts.get(TelecallingStatus.NOT_CALLED.value, 0)
    called = assigned - not_called
    confirmed = counts.get(TelecallingStatus.CONFIRMED.value, 0)
    not_connected_values = {status.value for status in _NOT_CONNECTED_OUTCOMES}
    connected = sum(count for status, count in counts.items() if status not in not_connected_values)
    return {
        "assigned": assigned,
        "called": called,
        "pending": not_called,
        "connected": connected,
        "interested": counts.get(TelecallingStatus.INTERESTED.value, 0),
        "follow_ups": follow_ups,
        "confirmed": confirmed,
        "not_interested": counts.get(TelecallingStatus.NOT_INTERESTED.value, 0),
        "conversion_rate": round((confirmed / assigned) * 100, 1) if assigned else 0.0,
    }


def _summary_from_counts(counts: dict[str, int], follow_ups_today: int) -> dict[str, int]:
    assigned = sum(counts.values())
    not_called = counts.get(TelecallingStatus.NOT_CALLED.value, 0)
    not_connected_values = {status.value for status in _NOT_CONNECTED_OUTCOMES}
    connected = sum(count for status, count in counts.items() if status not in not_connected_values)
    return {
        "assigned": assigned,
        "pending": not_called,
        "called": assigned - not_called,
        "connected": connected,
        "follow_ups_today": follow_ups_today,
        "confirmed": counts.get(TelecallingStatus.CONFIRMED.value, 0),
        "not_interested": counts.get(TelecallingStatus.NOT_INTERESTED.value, 0),
    }

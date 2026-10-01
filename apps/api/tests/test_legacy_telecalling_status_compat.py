"""Production incident, 2026-10-01: `GET /telecaller/orders` 500'd with
`LookupError: 'not_received' is not among the defined enum values. Enum
name: telecalling_status`.

Root cause: the 2026-09-30 call-status simplification trimmed
`TelecallingStatus` from 13 to 10 values and shipped (code deployed) before
its companion migration (`f3a7c9e1b6d2_simplify_telecalling_status.py`,
which remaps existing rows and narrows the Postgres enum type) was ever
run against production. Production's `order_assignments.current_status`/
`call_attempts.outcome` columns still hold real rows using the old,
now-missing values (`not_received`, `connected`, `call_attempted`,
`invalid_number`, `call_back_requested`, `follow_up_required`) -- and
SQLAlchemy's `Enum` type raises `LookupError` the instant it tries to
deserialize any of them into the (now too-narrow) Python enum.

Fix (`app/models/enums.py::TelecallingStatus`): those 6 values are kept as
real, readable enum members (LEGACY, see `LEGACY_TELECALLING_STATUSES`) --
never dropped, never renamed, never re-migrated here -- so existing
production rows deserialize exactly as they always did. They're excluded
from `CALL_OUTCOME_OPTIONS`/`OUTCOME_TAGS` and rejected by `LogCallRequest`
for any NEW call log (`_reject_legacy_outcomes`), so the UI/API-level
simplification is unaffected -- this is a read-compatibility fix only, not
a reversal of the simplification.

These tests simulate real pre-existing production data (an `OrderAssignment`/
`CallAttempt` row carrying a legacy outcome, created directly at the ORM
layer -- never through the API, since logging one is now correctly
rejected) and prove the real HTTP paths (`GET /telecaller/orders`,
`GET /telecaller/orders/{id}`, `GET /telecaller/orders/{id}/calls`) no
longer 500 for it -- not just an isolated enum unit test.
"""

from __future__ import annotations

import pytest
from app.db.session import get_db
from app.main import app
from app.models.enums import LEGACY_TELECALLING_STATUSES, OrderStatus, TelecallingStatus
from app.repositories.telecalling import CallAttemptRepository, OrderAssignmentRepository
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.telecalling_test_utils import (
    bearer_client,
    make_customer,
    make_order,
    make_role,
    make_user,
)

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _clear_get_db_override():
    yield
    app.dependency_overrides.clear()


async def _setup(db_session: AsyncSession):
    team_leader_role = await make_role(
        db_session, name="TEAM_LEADER", permission_codes=["telecalling.manage"]
    )
    telecaller_role = await make_role(
        db_session, name="TELECALLER", permission_codes=["calls.manage", "orders.confirm"]
    )
    leader = await make_user(db_session, email="leader@legacy.example.com", role=team_leader_role)
    telecaller = await make_user(
        db_session, email="tc@legacy.example.com", role=telecaller_role, team_leader_id=leader.id
    )
    other_telecaller = await make_user(
        db_session, email="tc2@legacy.example.com", role=telecaller_role, team_leader_id=leader.id
    )
    customer = await make_customer(db_session)
    return leader, telecaller, other_telecaller, customer


async def _assign(client, order_id: str, telecaller_id: str) -> None:
    response = await client.post(
        "/api/v1/team/orders/assign",
        json={"order_ids": [order_id], "mode": "manual", "telecaller_id": telecaller_id},
    )
    assert response.status_code == 201


async def _give_order_a_legacy_outcome(
    db_session: AsyncSession, order_id, telecaller_id, *, legacy_outcome: TelecallingStatus
) -> None:
    """Simulates a real pre-existing production row -- written directly at
    the ORM layer (never through the API, which correctly refuses to log a
    legacy outcome for a NEW call -- see `_reject_legacy_outcomes`).
    """
    assignments = OrderAssignmentRepository(db_session)
    assignment = await assignments.get_active_for_order(order_id)
    assert assignment is not None
    await assignments.update(assignment, current_status=legacy_outcome, attempt_count=1)

    attempts = CallAttemptRepository(db_session)
    await attempts.create(
        order_id=order_id,
        telecaller_id=telecaller_id,
        attempt_number=1,
        attempted_at=assignment.created_at,
        outcome=legacy_outcome,
        notes=None,
        next_follow_up_at=None,
    )
    await db_session.commit()


@pytest.mark.parametrize("legacy_outcome", sorted(LEGACY_TELECALLING_STATUSES, key=str))
async def test_telecaller_orders_list_survives_every_legacy_outcome(
    db_session: AsyncSession, legacy_outcome: TelecallingStatus
) -> None:
    """The exact production path: `GET /telecaller/orders` must not 500
    for an existing row carrying any of the 6 legacy outcomes.
    """
    leader, telecaller, _other, customer = await _setup(db_session)
    order = await make_order(
        db_session, order_number=f"LEGACY-{legacy_outcome.value}", customer=customer
    )
    async with bearer_client(app, get_db, db_session, leader.id) as leader_client:
        await _assign(leader_client, str(order.id), str(telecaller.id))

    await _give_order_a_legacy_outcome(
        db_session, order.id, telecaller.id, legacy_outcome=legacy_outcome
    )

    async with bearer_client(app, get_db, db_session, telecaller.id) as tc_client:
        list_response = await tc_client.get("/api/v1/telecaller/orders")
        assert list_response.status_code == 200
        row = next(r for r in list_response.json()["data"] if r["order_id"] == str(order.id))
        assert row["call_status"] == legacy_outcome.value

        detail_response = await tc_client.get(f"/api/v1/telecaller/orders/{order.id}")
        assert detail_response.status_code == 200
        assert detail_response.json()["data"]["call_status"] == legacy_outcome.value

        history_response = await tc_client.get(f"/api/v1/telecaller/orders/{order.id}/calls")
        assert history_response.status_code == 200
        assert history_response.json()["data"][0]["outcome"] == legacy_outcome.value


async def test_the_exact_reported_production_value_not_received(db_session: AsyncSession) -> None:
    """The literal production error: `'not_received' is not among the
    defined enum values`.
    """
    leader, telecaller, _other, customer = await _setup(db_session)
    order = await make_order(db_session, order_number="LEGACY-PROD-REPRO", customer=customer)
    async with bearer_client(app, get_db, db_session, leader.id) as leader_client:
        await _assign(leader_client, str(order.id), str(telecaller.id))

    await _give_order_a_legacy_outcome(
        db_session, order.id, telecaller.id, legacy_outcome=TelecallingStatus.NOT_RECEIVED
    )

    async with bearer_client(app, get_db, db_session, telecaller.id) as tc_client:
        response = await tc_client.get("/api/v1/telecaller/orders")
    assert response.status_code == 200


async def test_all_new_statuses_still_work_after_the_fix(db_session: AsyncSession) -> None:
    """Requirement 10: regression-proof the full currently-supported set
    (not just the legacy values) -- not_called (never logged, default),
    every new outcome, confirmed, and other all still round-trip.
    """
    leader, telecaller, _other, customer = await _setup(db_session)
    for outcome in (
        "not_answering",
        "busy",
        "switched_off",
        "call_back_later",
        "interested",
        "not_interested",
        "cancelled",
    ):
        order = await make_order(db_session, order_number=f"NEWSTATUS-{outcome}", customer=customer)
        async with bearer_client(app, get_db, db_session, leader.id) as leader_client:
            await _assign(leader_client, str(order.id), str(telecaller.id))
        async with bearer_client(app, get_db, db_session, telecaller.id) as tc_client:
            response = await tc_client.post(
                f"/api/v1/telecaller/orders/{order.id}/calls", json={"outcome": outcome}
            )
            assert response.status_code == 201

    order = await make_order(db_session, order_number="NEWSTATUS-other", customer=customer)
    async with bearer_client(app, get_db, db_session, leader.id) as leader_client:
        await _assign(leader_client, str(order.id), str(telecaller.id))
    async with bearer_client(app, get_db, db_session, telecaller.id) as tc_client:
        response = await tc_client.post(
            f"/api/v1/telecaller/orders/{order.id}/calls",
            json={"outcome": "other", "notes": "Needs manual follow-up."},
        )
        assert response.status_code == 201

    order = await make_order(
        db_session,
        order_number="NEWSTATUS-confirmed",
        customer=customer,
        status=OrderStatus.PENDING,
    )
    async with bearer_client(app, get_db, db_session, leader.id) as leader_client:
        await _assign(leader_client, str(order.id), str(telecaller.id))
    async with bearer_client(app, get_db, db_session, telecaller.id) as tc_client:
        response = await tc_client.post(
            f"/api/v1/telecaller/orders/{order.id}/calls", json={"outcome": "confirmed"}
        )
        assert response.status_code == 201

    async with bearer_client(app, get_db, db_session, telecaller.id) as tc_client:
        listing = await tc_client.get("/api/v1/telecaller/orders")
        assert listing.status_code == 200


@pytest.mark.parametrize("legacy_outcome", sorted(LEGACY_TELECALLING_STATUSES, key=str))
async def test_legacy_outcomes_cannot_be_newly_logged(
    db_session: AsyncSession, legacy_outcome: TelecallingStatus
) -> None:
    """Legacy values are readable (existing data), never writable (a
    telecaller can't newly choose one) -- the simplification's intent is
    unaffected by this compatibility fix.
    """
    leader, telecaller, _other, customer = await _setup(db_session)
    order = await make_order(
        db_session, order_number=f"NOWRITE-{legacy_outcome.value}", customer=customer
    )
    async with bearer_client(app, get_db, db_session, leader.id) as leader_client:
        await _assign(leader_client, str(order.id), str(telecaller.id))

    async with bearer_client(app, get_db, db_session, telecaller.id) as tc_client:
        response = await tc_client.post(
            f"/api/v1/telecaller/orders/{order.id}/calls", json={"outcome": legacy_outcome.value}
        )
    assert response.status_code == 422


async def test_ownership_rbac_unchanged_for_an_order_with_a_legacy_outcome(
    db_session: AsyncSession,
) -> None:
    """RBAC/ownership scoping must behave identically regardless of
    whether the order happens to carry a legacy outcome.
    """
    leader, telecaller, other_telecaller, customer = await _setup(db_session)
    order = await make_order(db_session, order_number="LEGACY-RBAC", customer=customer)
    async with bearer_client(app, get_db, db_session, leader.id) as leader_client:
        await _assign(leader_client, str(order.id), str(telecaller.id))

    await _give_order_a_legacy_outcome(
        db_session, order.id, telecaller.id, legacy_outcome=TelecallingStatus.NOT_RECEIVED
    )

    async with bearer_client(app, get_db, db_session, other_telecaller.id) as other_client:
        response = await other_client.get(f"/api/v1/telecaller/orders/{order.id}")
    assert response.status_code == 403

    async with bearer_client(app, get_db, db_session, telecaller.id) as tc_client:
        response = await tc_client.get(f"/api/v1/telecaller/orders/{order.id}")
    assert response.status_code == 200


async def test_unauthenticated_request_still_gets_401_not_500(db_session: AsyncSession) -> None:
    async def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as anon_client:
        response = await anon_client.get("/api/v1/telecaller/orders")
    assert response.status_code == 401

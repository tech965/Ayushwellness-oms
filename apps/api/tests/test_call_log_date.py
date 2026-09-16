"""Requirement 3 (review meeting): "Log Call" must default the call date
to today/system date without the telecaller ever entering it, and editing
an OLD call record must never overwrite its original historical date.

`LogCallRequest` (`app/schemas/telecalling.py`) has no client-supplied
date field at all -- `CallAttempt.attempted_at` is always stamped
server-side with `datetime.now(UTC)` in `TelecallingService.log_call`, so
there is nothing for a telecaller to fill in and nothing to default in
the UI. This file locks that contract in place with a real HTTP
round-trip so a future change can't quietly reintroduce a manual/blank
date field or start trusting a client-supplied one.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from app.db.session import get_db
from app.main import app
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
    telecaller_role = await make_role(
        db_session, name="TELECALLER", permission_codes=["calls.manage"]
    )
    leader = await make_user(db_session, email="leader@call-date.example.com", is_superuser=True)
    telecaller = await make_user(
        db_session,
        email="tc@call-date.example.com",
        role=telecaller_role,
        team_leader_id=leader.id,
    )
    customer = await make_customer(db_session)
    order = await make_order(db_session, order_number="CALL-DATE-001", customer=customer)
    return leader, telecaller, order


async def _assign(client, order_id: str, telecaller_id: str) -> None:
    response = await client.post(
        "/api/v1/team/orders/assign",
        json={"order_ids": [order_id], "mode": "manual", "telecaller_id": telecaller_id},
    )
    assert response.status_code == 201


async def test_a_new_call_log_defaults_attempted_at_to_now_without_a_client_supplied_date(
    db_session: AsyncSession,
) -> None:
    leader, telecaller, order = await _setup(db_session)
    async with bearer_client(app, get_db, db_session, leader.id) as leader_client:
        await _assign(leader_client, str(order.id), str(telecaller.id))

    before = datetime.now(UTC)
    async with bearer_client(app, get_db, db_session, telecaller.id) as tc_client:
        # No date/time field in the request at all -- the telecaller never
        # enters one, and the API doesn't accept one.
        response = await tc_client.post(
            f"/api/v1/telecaller/orders/{order.id}/calls",
            json={"outcome": "connected", "notes": "Answered."},
        )
    after = datetime.now(UTC)

    assert response.status_code == 201
    attempted_at = datetime.fromisoformat(response.json()["data"]["attempted_at"])
    assert before - timedelta(seconds=5) <= attempted_at <= after + timedelta(seconds=5)


async def test_editing_an_old_call_record_never_overwrites_its_historical_date(
    db_session: AsyncSession,
) -> None:
    leader, telecaller, order = await _setup(db_session)
    async with bearer_client(app, get_db, db_session, leader.id) as leader_client:
        await _assign(leader_client, str(order.id), str(telecaller.id))

    async with bearer_client(app, get_db, db_session, telecaller.id) as tc_client:
        logged = await tc_client.post(
            f"/api/v1/telecaller/orders/{order.id}/calls",
            json={"outcome": "not_received", "notes": "No answer."},
        )
        assert logged.status_code == 201
        original_attempted_at = logged.json()["data"]["attempted_at"]
        attempt_id = logged.json()["data"]["id"]

        # A little time passes before the correction is made -- if the
        # edit re-stamped `attempted_at`, this new value would provably
        # differ from the original.
        edited = await tc_client.patch(
            f"/api/v1/telecaller/orders/{order.id}/calls/{attempt_id}",
            json={"outcome": "connected", "notes": "Actually reached them on redial."},
        )

    assert edited.status_code == 200
    data = edited.json()["data"]
    assert data["attempted_at"] == original_attempted_at
    assert data["is_edited"] is True
    assert data["outcome"] == "connected"

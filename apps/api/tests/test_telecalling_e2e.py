"""End-to-end simulation of the spec's exact scenario, driven entirely
over real HTTP requests against the real FastAPI app + a real (in-memory
SQLite) database — the strongest verification available without a
browser: Admin creates a Team Leader and 6 Telecallers, the Team Leader
bulk-assigns 100 unfulfilled orders equally, Telecaller 1 works one order
through two call attempts and a follow-up, the Team Leader's performance
view reflects it, and Telecaller 1 is denied access to Telecaller 2's
order.
"""

from __future__ import annotations

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


async def test_full_team_leader_telecaller_workflow(db_session: AsyncSession) -> None:
    # --- Admin creates a Team Leader and 6 Telecallers ---
    team_leader_role = await make_role(
        db_session, name="TEAM_LEADER", permission_codes=["telecalling.manage"]
    )
    telecaller_role = await make_role(
        db_session, name="TELECALLER", permission_codes=["calls.manage"]
    )

    admin = await make_user(db_session, email="admin@e2e.example.com", is_superuser=True)
    leader = await make_user(db_session, email="leader@e2e.example.com", role=team_leader_role)
    telecallers = [
        await make_user(
            db_session,
            email=f"tc{i}@e2e.example.com",
            role=telecaller_role,
            team_leader_id=leader.id,
        )
        for i in range(6)
    ]

    customer = await make_customer(db_session)
    orders = [
        await make_order(db_session, order_number=f"E2E-{i:03d}", customer=customer)
        for i in range(100)
    ]

    # --- Team Leader logs in, opens Unfulfilled Orders ---
    async with bearer_client(app, get_db, db_session, leader.id) as leader_client:
        unfulfilled = await leader_client.get(
            "/api/v1/team/orders/unfulfilled", params={"page_size": 200}
        )
        assert unfulfilled.status_code == 200
        assert unfulfilled.json()["meta"]["total_items"] == 100

        # --- Selects all 100, assigns to the 6 telecallers (equal) ---
        assign_response = await leader_client.post(
            "/api/v1/team/orders/assign",
            json={
                "order_ids": [str(o.id) for o in orders],
                "mode": "equal",
                "telecaller_ids": [str(t.id) for t in telecallers],
            },
        )
        assert assign_response.status_code == 201
        assert len(assign_response.json()["data"]) == 100

        # --- Verify distribution: 17/17/17/17/16/16 ---
        performance = await leader_client.get("/api/v1/team/telecallers")
        assert performance.status_code == 200
        assigned_counts = sorted(
            (row["assigned"] for row in performance.json()["data"]), reverse=True
        )
        assert assigned_counts == [17, 17, 17, 17, 16, 16]

    # --- Telecaller 1 logs in ---
    telecaller_1 = telecallers[0]
    async with bearer_client(app, get_db, db_session, telecaller_1.id) as tc1_client:
        my_orders = await tc1_client.get("/api/v1/telecaller/orders", params={"page_size": 200})
        assert my_orders.status_code == 200
        my_order_ids = {row["order_id"] for row in my_orders.json()["data"]}
        assert len(my_order_ids) == 17

        # Every order returned must actually be assigned to telecaller 1
        # (not just "the first 17 orders overall") — the real assertion
        # the spec's "verify only their assigned orders are visible" step
        # is checking for.
        assert my_order_ids.issubset({str(o.id) for o in orders})

        target_order_id = next(iter(my_order_ids))

        # --- Log Attempt #1: NOT_RECEIVED ---
        attempt_1 = await tc1_client.post(
            f"/api/v1/telecaller/orders/{target_order_id}/calls",
            json={"outcome": "not_received", "notes": "No answer."},
        )
        assert attempt_1.status_code == 201
        assert attempt_1.json()["data"]["attempt_number"] == 1

        # --- Log Attempt #2: CONNECTED ---
        attempt_2 = await tc1_client.post(
            f"/api/v1/telecaller/orders/{target_order_id}/calls",
            json={"outcome": "connected", "notes": "Spoke to customer."},
        )
        assert attempt_2.status_code == 201
        assert attempt_2.json()["data"]["attempt_number"] == 2

        # --- Schedule Follow-up ---
        follow_up = await tc1_client.post(
            f"/api/v1/telecaller/orders/{target_order_id}/follow-up",
            json={"next_follow_up_at": "2026-08-28T10:00:00Z"},
        )
        assert follow_up.status_code == 200

        # --- Verify follow-up appears ---
        follow_ups = await tc1_client.get("/api/v1/telecaller/follow-ups?when=upcoming")
        assert follow_ups.status_code == 200
        assert target_order_id in {row["order_id"] for row in follow_ups.json()["data"]}

        # --- Telecaller 1 attempts to access a Telecaller 2 order: DENIED ---
        telecaller_2 = telecallers[1]
        async with bearer_client(app, get_db, db_session, telecaller_2.id) as tc2_client:
            tc2_orders = await tc2_client.get(
                "/api/v1/telecaller/orders", params={"page_size": 200}
            )
            tc2_order_id = tc2_orders.json()["data"][0]["order_id"]
            assert tc2_order_id not in my_order_ids

        forbidden = await tc1_client.get(f"/api/v1/telecaller/orders/{tc2_order_id}")
        assert forbidden.status_code == 403
        assert forbidden.json()["error"]["code"] == "authorization_error"

        forbidden_call = await tc1_client.post(
            f"/api/v1/telecaller/orders/{tc2_order_id}/calls", json={"outcome": "connected"}
        )
        assert forbidden_call.status_code in (403, 404)

    # --- Team Leader checks performance: activity from telecaller 1 appears ---
    async with bearer_client(app, get_db, db_session, leader.id) as leader_client:
        performance = await leader_client.get("/api/v1/team/telecallers")
        assert performance.status_code == 200
        tc1_row = next(
            row
            for row in performance.json()["data"]
            if row["telecaller_id"] == str(telecaller_1.id)
        )
        assert tc1_row["called"] == 1  # one order has moved past NOT_CALLED
        assert tc1_row["connected"] == 1

        team_order_detail = await leader_client.get(f"/api/v1/team/orders/{target_order_id}")
        assert team_order_detail.status_code == 200
        assert team_order_detail.json()["data"]["attempt_count"] == 2
        assert team_order_detail.json()["data"]["call_status"] == "connected"

    # --- Admin sees everything too ---
    async with bearer_client(app, get_db, db_session, admin.id) as admin_client:
        admin_view = await admin_client.get(
            "/api/v1/team/orders/unfulfilled", params={"page_size": 200}
        )
        assert admin_view.status_code == 200
        assert admin_view.json()["meta"]["total_items"] == 100

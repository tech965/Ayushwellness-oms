"""Shipment Staff — a dedicated, SCOPED shipment-processing experience.

MOST IMPORTANT property under test: a Shipment Staff user sees ONLY
shipments/orders for Telecallers assigned to them
(`User.shipment_staff_id`), enforced at the query/API level (never just
hidden in the UI) -- and ADMIN/the existing unscoped endpoints continue
to see everything, completely unaffected by this feature.
"""

from __future__ import annotations

import pytest
from app.db.session import get_db
from app.main import app
from app.models.enums import NDRStatus, OrderStatus, ShipmentStatus
from app.models.ndr import NDR
from app.models.shipment import Shipment
from app.repositories.auth import UserRepository
from sqlalchemy.ext.asyncio import AsyncSession

from tests.telecalling_test_utils import (
    bearer_client,
    make_customer,
    make_order,
    make_role,
    make_user,
)

pytestmark = pytest.mark.asyncio

SHIPMENT_STAFF_PERMISSIONS = ["shipment_staff.manage"]
TELECALLER_PERMISSIONS = ["calls.manage", "orders.read", "orders.confirm"]
FULFILLMENT_PERMISSIONS = [
    "orders.read",
    "shipments.read",
    "shipments.update",
    "ndr.read",
    "rto.read",
]


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


async def _assign_to_shipment_staff(
    db_session: AsyncSession, *, telecaller_id, shipment_staff_id
) -> None:
    users = UserRepository(db_session)
    telecaller = await users.get_by_id(telecaller_id)
    await users.update(telecaller, shipment_staff_id=shipment_staff_id)
    await db_session.commit()


async def _setup(db_session: AsyncSession):
    """Telecaller A + Telecaller B (same Team Leader), Shipment Staff A
    (assigned only Telecaller A), a plain admin, and one CONFIRMED order
    per telecaller.
    """
    telecaller_role = await make_role(
        db_session, name="TELECALLER", permission_codes=TELECALLER_PERMISSIONS
    )
    team_leader_role = await make_role(
        db_session, name="TEAM_LEADER", permission_codes=["telecalling.manage"]
    )
    shipment_staff_role = await make_role(
        db_session, name="SHIPMENT_STAFF", permission_codes=SHIPMENT_STAFF_PERMISSIONS
    )
    leader = await make_user(db_session, email="leader@ss.example.com", role=team_leader_role)
    telecaller_a = await make_user(
        db_session, email="tca@ss.example.com", role=telecaller_role, team_leader_id=leader.id
    )
    telecaller_b = await make_user(
        db_session, email="tcb@ss.example.com", role=telecaller_role, team_leader_id=leader.id
    )
    staff_a = await make_user(db_session, email="staffa@ss.example.com", role=shipment_staff_role)
    admin = await make_user(db_session, email="admin@ss.example.com", is_superuser=True)

    await _assign_to_shipment_staff(
        db_session, telecaller_id=telecaller_a.id, shipment_staff_id=staff_a.id
    )

    customer = await make_customer(db_session)
    order_a = await make_order(
        db_session, order_number="SS-ORDER-A", customer=customer, status=OrderStatus.PENDING
    )
    order_b = await make_order(
        db_session, order_number="SS-ORDER-B", customer=customer, status=OrderStatus.PENDING
    )

    async with bearer_client(app, get_db, db_session, leader.id) as leader_client:
        for order, telecaller in ((order_a, telecaller_a), (order_b, telecaller_b)):
            assign = await leader_client.post(
                "/api/v1/team/orders/assign",
                json={
                    "order_ids": [str(order.id)],
                    "mode": "manual",
                    "telecaller_id": str(telecaller.id),
                },
            )
            assert assign.status_code == 201

    async with bearer_client(app, get_db, db_session, telecaller_a.id) as tc_a_client:
        confirm_a = await tc_a_client.post(f"/api/v1/telecaller/orders/{order_a.id}/confirm")
        assert confirm_a.status_code == 200
    async with bearer_client(app, get_db, db_session, telecaller_b.id) as tc_b_client:
        confirm_b = await tc_b_client.post(f"/api/v1/telecaller/orders/{order_b.id}/confirm")
        assert confirm_b.status_code == 200

    return {
        "leader": leader,
        "telecaller_a": telecaller_a,
        "telecaller_b": telecaller_b,
        "staff_a": staff_a,
        "admin": admin,
        "order_a": order_a,
        "order_b": order_b,
    }


# ---------------------------------------------------------------------
# Case A / B — the core isolation property
# ---------------------------------------------------------------------


async def test_case_a_shipment_staff_sees_order_confirmed_by_their_own_telecaller(
    db_session: AsyncSession,
) -> None:
    ctx = await _setup(db_session)
    async with bearer_client(app, get_db, db_session, ctx["staff_a"].id) as client:
        response = await client.get("/api/v1/shipment-staff/orders")
        assert response.status_code == 200
        order_ids = {row["order_id"] for row in response.json()["data"]}
        assert str(ctx["order_a"].id) in order_ids


async def test_case_b_shipment_staff_does_not_see_order_confirmed_by_another_telecaller(
    db_session: AsyncSession,
) -> None:
    ctx = await _setup(db_session)
    async with bearer_client(app, get_db, db_session, ctx["staff_a"].id) as client:
        response = await client.get("/api/v1/shipment-staff/orders")
        assert response.status_code == 200
        order_ids = {row["order_id"] for row in response.json()["data"]}
        assert str(ctx["order_b"].id) not in order_ids


async def test_shipment_staff_with_no_telecaller_assigned_sees_nothing(
    db_session: AsyncSession,
) -> None:
    """The safe default: an unassigned Shipment Staff user sees an empty
    scope, never everything.
    """
    ctx = await _setup(db_session)
    role = await make_role(
        db_session, name="SHIPMENT_STAFF", permission_codes=SHIPMENT_STAFF_PERMISSIONS
    )
    unassigned_staff = await make_user(db_session, email="unassigned@ss.example.com", role=role)
    async with bearer_client(app, get_db, db_session, unassigned_staff.id) as client:
        response = await client.get("/api/v1/shipment-staff/orders")
        assert response.status_code == 200
        assert response.json()["data"] == []
    assert ctx["order_a"].id  # keep ctx referenced


# ---------------------------------------------------------------------
# Case C — cannot bypass scope via direct id / URL manipulation
# ---------------------------------------------------------------------


async def test_case_c_direct_access_to_out_of_scope_order_is_rejected(
    db_session: AsyncSession,
) -> None:
    ctx = await _setup(db_session)
    async with bearer_client(app, get_db, db_session, ctx["staff_a"].id) as client:
        response = await client.get(f"/api/v1/shipment-staff/orders/{ctx['order_b'].id}")
        assert response.status_code == 403


async def test_shipment_staff_cannot_process_shipment_for_out_of_scope_order(
    db_session: AsyncSession,
) -> None:
    ctx = await _setup(db_session)
    async with bearer_client(app, get_db, db_session, ctx["staff_a"].id) as client:
        response = await client.post(
            f"/api/v1/shipment-staff/orders/{ctx['order_b'].id}/ship", json={}
        )
        assert response.status_code == 403


async def test_shipment_staff_cannot_operate_on_another_scopes_shipment(
    db_session: AsyncSession,
) -> None:
    ctx = await _setup(db_session)
    shipment_b = Shipment(
        order_id=ctx["order_b"].id, current_status=ShipmentStatus.PENDING, source_system="manual"
    )
    db_session.add(shipment_b)
    await db_session.commit()
    await db_session.refresh(shipment_b)

    async with bearer_client(app, get_db, db_session, ctx["staff_a"].id) as client:
        get_response = await client.get(f"/api/v1/shipment-staff/shipments/{shipment_b.id}")
        assert get_response.status_code == 403

        awb_response = await client.post(
            f"/api/v1/shipment-staff/shipments/{shipment_b.id}/assign-awb", json={}
        )
        assert awb_response.status_code == 403

        pickup_response = await client.post(
            f"/api/v1/shipment-staff/shipments/{shipment_b.id}/request-pickup"
        )
        assert pickup_response.status_code == 403


async def test_query_parameters_cannot_widen_shipment_staff_scope(
    db_session: AsyncSession,
) -> None:
    """The queue endpoint takes no client-suppliable scope parameter at
    all -- confirming a URL/query-string trick has nothing to attach to.
    Scope is derived purely from the authenticated user server-side.
    """
    ctx = await _setup(db_session)
    async with bearer_client(app, get_db, db_session, ctx["staff_a"].id) as client:
        response = await client.get(
            "/api/v1/shipment-staff/orders",
            params={"telecaller_id": str(ctx["telecaller_b"].id)},
        )
        assert response.status_code == 200
        order_ids = {row["order_id"] for row in response.json()["data"]}
        assert str(ctx["order_b"].id) not in order_ids


# ---------------------------------------------------------------------
# Case D — Admin sees everything, unaffected
# ---------------------------------------------------------------------


async def test_case_d_admin_sees_orders_from_every_telecaller(db_session: AsyncSession) -> None:
    ctx = await _setup(db_session)
    async with bearer_client(app, get_db, db_session, ctx["admin"].id) as client:
        response_a = await client.get(f"/api/v1/orders/{ctx['order_a'].id}")
        response_b = await client.get(f"/api/v1/orders/{ctx['order_b'].id}")
        assert response_a.status_code == 200
        assert response_b.status_code == 200


async def test_fulfillment_role_still_sees_every_confirmed_order_unscoped(
    db_session: AsyncSession,
) -> None:
    """FULFILLMENT's existing behavior must be completely unaffected by
    this feature -- still org-wide, never scoped.
    """
    ctx = await _setup(db_session)
    role = await make_role(
        db_session, name="FULFILLMENT", permission_codes=FULFILLMENT_PERMISSIONS
    )
    fulfillment_user = await make_user(db_session, email="fulfil@ss.example.com", role=role)
    async with bearer_client(app, get_db, db_session, fulfillment_user.id) as client:
        response = await client.get("/api/v1/shipments/queue")
        order_ids = {row["order_id"] for row in response.json()["data"]}
        assert str(ctx["order_a"].id) in order_ids
        assert str(ctx["order_b"].id) in order_ids


# ---------------------------------------------------------------------
# Case E / F — dashboards
# ---------------------------------------------------------------------


async def test_case_e_admin_shipment_dashboard_shows_global_data(
    db_session: AsyncSession,
) -> None:
    ctx = await _setup(db_session)
    async with bearer_client(app, get_db, db_session, ctx["admin"].id) as client:
        response = await client.get("/api/v1/shipments/summary")
        assert response.status_code == 200
        # Both order A and order B are confirmed and awaiting shipment --
        # the unscoped admin dashboard must count both.
        assert response.json()["data"]["confirmed_awaiting_shipment"] >= 2


async def test_case_f_shipment_staff_dashboard_shows_only_scoped_data(
    db_session: AsyncSession,
) -> None:
    ctx = await _setup(db_session)
    async with bearer_client(app, get_db, db_session, ctx["staff_a"].id) as client:
        summary = await client.get("/api/v1/shipment-staff/summary")
        assert summary.status_code == 200
        # Only order A (Telecaller A, staff_a's scope) counts -- order B
        # (Telecaller B) must not inflate this Shipment Staff's numbers.
        assert summary.json()["data"]["confirmed_awaiting_shipment"] == 1

        analytics = await client.get("/api/v1/shipment-staff/analytics")
        assert analytics.status_code == 200
        telecaller_ids = {t["telecaller_id"] for t in analytics.json()["data"]["telecaller_stats"]}
        assert str(ctx["telecaller_a"].id) in telecaller_ids
        assert str(ctx["telecaller_b"].id) not in telecaller_ids


# ---------------------------------------------------------------------
# Case G — full shipment lifecycle stays correctly scoped at every stage
# ---------------------------------------------------------------------


async def test_case_g_shipment_lifecycle_stays_scoped_at_every_stage(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.core.config import settings
    from app.integrations.registry import clear_adapters, register_adapter
    from app.integrations.shiprocket.adapter import ShiprocketAdapter

    monkeypatch.setattr(settings, "SHIPROCKET_EMAIL", "ops@example.com")
    monkeypatch.setattr(settings, "SHIPROCKET_PASSWORD", "secret")
    monkeypatch.setattr(settings, "SHIPROCKET_PICKUP_LOCATION", "Main Warehouse")

    class _StubClient:
        def __init__(self, responses):
            self._responses = list(responses)

        async def request(self, method, path, *, json=None, params=None):
            return self._responses.pop(0)

        async def ensure_authenticated(self):
            pass

    register_adapter(
        ShiprocketAdapter(
            client=_StubClient(
                [
                    {"order_id": "9001", "shipment_id": "5001", "status": "NEW"},
                    {
                        "response": {
                            "data": {
                                "awb_code": "AWB777",
                                "courier_name": "Delhivery",
                                "courier_company_id": "51",
                            }
                        }
                    },
                ]
            )
        )
    )

    ctx = await _setup(db_session)
    # `_check_shippable` now also requires a shipping address on file --
    # `_setup`'s orders never set one (only telecaller confirmation is
    # exercised there); set it directly here since this is the one test
    # that actually ships.
    ctx["order_a"].shipping_address = {"line1": "123 Test St", "city": "Mumbai", "pin_code": "400001"}
    await db_session.commit()
    async with bearer_client(app, get_db, db_session, ctx["staff_a"].id) as client:
        ship = await client.post(f"/api/v1/shipment-staff/orders/{ctx['order_a'].id}/ship", json={})
        assert ship.status_code == 201
        shipment_id = ship.json()["data"]["id"]
        assert ship.json()["data"]["current_status"] == "pending"

        detail_after_create = await client.get(f"/api/v1/shipment-staff/shipments/{shipment_id}")
        assert detail_after_create.status_code == 200
        assert detail_after_create.json()["data"]["awb"] is None

        awb = await client.post(
            f"/api/v1/shipment-staff/shipments/{shipment_id}/assign-awb", json={}
        )
        assert awb.status_code == 200
        assert awb.json()["data"]["awb"] == "AWB777"

    clear_adapters()


async def test_ship_via_shipment_staff_never_creates_a_duplicate_shipment(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same idempotency guard `ShiprocketOperationsService.create_shipment_for_order`
    already enforces (a second create call is rejected before ever
    calling Shiprocket again) applies unchanged through the scoped router.
    """
    from app.core.config import settings
    from app.integrations.registry import clear_adapters, register_adapter
    from app.integrations.shiprocket.adapter import ShiprocketAdapter

    monkeypatch.setattr(settings, "SHIPROCKET_EMAIL", "ops@example.com")
    monkeypatch.setattr(settings, "SHIPROCKET_PASSWORD", "secret")
    monkeypatch.setattr(settings, "SHIPROCKET_PICKUP_LOCATION", "Main Warehouse")

    class _StubClient:
        def __init__(self, responses):
            self._responses = list(responses)

        async def request(self, method, path, *, json=None, params=None):
            return self._responses.pop(0)

        async def ensure_authenticated(self):
            pass

    register_adapter(
        ShiprocketAdapter(
            client=_StubClient([{"order_id": "9001", "shipment_id": "5001", "status": "NEW"}])
        )
    )

    ctx = await _setup(db_session)
    ctx["order_a"].shipping_address = {"line1": "123 Test St", "city": "Mumbai", "pin_code": "400001"}
    await db_session.commit()
    async with bearer_client(app, get_db, db_session, ctx["staff_a"].id) as client:
        first = await client.post(
            f"/api/v1/shipment-staff/orders/{ctx['order_a'].id}/ship", json={}
        )
        assert first.status_code == 201

        second = await client.post(
            f"/api/v1/shipment-staff/orders/{ctx['order_a'].id}/ship", json={}
        )
        assert second.status_code == 409

    clear_adapters()


# ---------------------------------------------------------------------
# Case H — NDR/RTO scoping
# ---------------------------------------------------------------------


async def test_case_h_shipment_staff_sees_only_scoped_ndr_admin_sees_all(
    db_session: AsyncSession,
) -> None:
    ctx = await _setup(db_session)
    shipment_a = Shipment(
        order_id=ctx["order_a"].id, current_status=ShipmentStatus.NDR, source_system="manual"
    )
    shipment_b = Shipment(
        order_id=ctx["order_b"].id, current_status=ShipmentStatus.NDR, source_system="manual"
    )
    db_session.add_all([shipment_a, shipment_b])
    await db_session.flush()
    ndr_a = NDR(order_id=ctx["order_a"].id, shipment_id=shipment_a.id, status=NDRStatus.OPEN)
    ndr_b = NDR(order_id=ctx["order_b"].id, shipment_id=shipment_b.id, status=NDRStatus.OPEN)
    db_session.add_all([ndr_a, ndr_b])
    await db_session.commit()

    async with bearer_client(app, get_db, db_session, ctx["staff_a"].id) as staff_client:
        response = await staff_client.get("/api/v1/shipment-staff/ndr")
        assert response.status_code == 200
        order_numbers = {row["order_number"] for row in response.json()["data"]}
        assert "SS-ORDER-A" in order_numbers
        assert "SS-ORDER-B" not in order_numbers

    role = await make_role(db_session, name="OPERATIONS", permission_codes=["ndr.read"])
    ops_user = await make_user(db_session, email="ops-ndr@ss.example.com", role=role)
    async with bearer_client(app, get_db, db_session, ops_user.id) as ops_client:
        response = await ops_client.get("/api/v1/ndr")
        assert response.status_code == 200
        order_numbers = {row["order_number"] for row in response.json()["data"]}
        assert "SS-ORDER-A" in order_numbers
        assert "SS-ORDER-B" in order_numbers


# ---------------------------------------------------------------------
# RBAC
# ---------------------------------------------------------------------


async def test_telecaller_cannot_access_shipment_staff_endpoints(
    db_session: AsyncSession,
) -> None:
    ctx = await _setup(db_session)
    async with bearer_client(app, get_db, db_session, ctx["telecaller_a"].id) as client:
        response = await client.get("/api/v1/shipment-staff/orders")
        assert response.status_code == 403


async def test_seed_shipment_staff_role_permission_grid() -> None:
    from scripts.seed import ROLE_PERMISSIONS

    granted = set(ROLE_PERMISSIONS["SHIPMENT_STAFF"])
    assert granted == {"shipment_staff.manage"}
    # Critically must never grant the unscoped general permissions --
    # that would let a Shipment Staff user bypass their scope entirely
    # via the existing /orders, /shipments, /ndr, /rto endpoints.
    for forbidden in ("orders.read", "shipments.read", "shipments.update", "ndr.read", "rto.read"):
        assert forbidden not in granted

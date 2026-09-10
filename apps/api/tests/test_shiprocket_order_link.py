"""Fulfillment Queue "Process Shipment"/"Ship Order" now open the real
Shiprocket "Ready to Ship" page (pre-filtered to one order via
`order_ids`) in a new tab, instead of calling `ShiprocketOperationsService.
create_shipment_for_order` again -- doing so would risk creating a
second, duplicate Shiprocket order/shipment for one Shiprocket may
already have (e.g. via its own Shopify channel connector, independent of
this OMS ever pushing anything). Previously this opened the individual
order-details page (`.../orders/details/{id}`) -- only the URL template
changed; the id lookup itself (this whole file) is unchanged.

`shiprocket_order_url` (`app.services.shiprocket_service`) is the single
place this URL is built, from the real Shiprocket order id
`create_shipment_for_order` already persists in `Shipment.raw_external_
payload["order_id"]` -- never derived from the OMS order number, and
never guessed when that id genuinely isn't stored.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from app.db.session import get_db
from app.integrations.registry import clear_adapters, register_adapter
from app.integrations.shiprocket.adapter import ShiprocketAdapter
from app.main import app
from app.models.enums import OrderStatus, PaymentType, ShipmentStatus
from app.models.shipment import Shipment
from app.repositories.shipment import ShipmentRepository
from app.services.order_service import OrderService
from app.services.shiprocket_service import (
    SHIPROCKET_READY_TO_SHIP_URL,
    locate_shiprocket_orders,
    shiprocket_order_url,
)
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


class _StubClient:
    """Mirrors the stub client used across the other Shiprocket test
    files (`test_shiprocket_operations.py`/`test_shiprocket_sync.py`) --
    hand-queued responses, no real HTTP.
    """

    def __init__(self, responses: list) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[str, str, dict | None]] = []

    async def request(
        self, method: str, path: str, *, json: dict | None = None, params: dict | None = None
    ) -> dict:
        self.calls.append((method, path, json))
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    async def ensure_authenticated(self) -> None:
        pass


@pytest.fixture(autouse=True)
def _reset_adapter_registry():
    yield
    clear_adapters()


def _shipments_page(*, records: list[dict], total_pages: int = 1) -> dict:
    return {"data": records, "meta": {"pagination": {"total_pages": total_pages}}}


def _orders_show_response(channel_order_id: object) -> dict:
    return {"data": {"channel_order_id": channel_order_id}}


async def _make_bare_order(session: AsyncSession, *, order_number: str):
    return await OrderService(session).create_order(
        actor=None,
        order_number=order_number,
        customer_id=None,
        order_datetime=datetime.now(UTC),
        currency="INR",
        payment_type=PaymentType.PREPAID,
        shipping_charge=Decimal("0"),
        notes=None,
        items=[],
    )


async def _make_ops_user(db_session: AsyncSession):
    role = await make_role(
        db_session, name="OPERATIONS", permission_codes=["shipments.read", "shipments.update"]
    )
    return await make_user(db_session, email="ops@shiprocket-link.example.com", role=role)


# --- shiprocket_order_url() unit tests ----------------------------------


def test_shiprocket_order_url_none_for_no_shipment() -> None:
    assert shiprocket_order_url(None) is None


def test_shiprocket_order_url_none_when_no_raw_payload_stored() -> None:
    shipment = Shipment(shiprocket_shipment_id="5001", raw_external_payload=None)
    assert shiprocket_order_url(shipment) is None


def test_shiprocket_order_url_none_when_payload_has_no_order_id() -> None:
    """A pulled-in shipment this OMS never created itself (e.g. one
    Shiprocket's own Shopify channel connector already had) -- see
    `shiprocket_order_url`'s docstring on why this must never guess.
    """
    shipment = Shipment(shiprocket_shipment_id="5001", raw_external_payload={"shipment_id": "5001"})
    assert shiprocket_order_url(shipment) is None


def test_shiprocket_order_url_builds_the_ready_to_ship_url_from_the_stored_order_id() -> None:
    """Requirement A: a valid, locally-stored Shiprocket order id builds
    the "Ready to Ship" URL, `order_ids={numeric_id}` -- never the old
    individual order-details page.
    """
    shipment = Shipment(
        shiprocket_shipment_id="5001",
        raw_external_payload={"order_id": "1576398335", "shipment_id": "5001", "status": "NEW"},
    )
    assert shiprocket_order_url(shipment) == f"{SHIPROCKET_READY_TO_SHIP_URL}?order_ids=1576398335"
    assert (
        shiprocket_order_url(shipment)
        == "https://app.shiprocket.in/seller/orders/readytoship?order_ids=1576398335"
    )


def test_shiprocket_order_url_never_derived_from_the_oms_order_number() -> None:
    """Requirement B: sanity guard against the literal mistake the spec
    calls out -- the OMS order number (`#AWLxxxxx`) never appears
    anywhere in the computation, only the stored Shiprocket `order_id`,
    and it's always passed as the `order_ids` query param, never a path
    segment.
    """
    shipment = Shipment(
        shiprocket_shipment_id="5001",
        raw_external_payload={"order_id": "1576398335"},
    )
    url = shiprocket_order_url(shipment)
    assert url is not None
    assert "AWL" not in url
    assert "#" not in url
    assert url == "https://app.shiprocket.in/seller/orders/readytoship?order_ids=1576398335"


# --- Exposed on the Shipment Queue ("Orders Need Shipment") -------------


async def test_shipment_queue_exposes_the_real_shiprocket_order_url(
    db_session: AsyncSession,
) -> None:
    ops = await _make_ops_user(db_session)
    customer = await make_customer(db_session)
    order = await make_order(
        db_session, order_number="LINK-001", customer=customer, status=OrderStatus.CONFIRMED
    )
    shipment = Shipment(
        order_id=order.id,
        current_status=ShipmentStatus.PENDING,
        source_system="shiprocket",
        external_id="5001",
        shiprocket_shipment_id="5001",
        raw_external_payload={"order_id": "1576398335", "shipment_id": "5001"},
    )
    db_session.add(shipment)
    await db_session.commit()

    async with bearer_client(app, get_db, db_session, ops.id) as client:
        response = await client.get("/api/v1/shipments/queue")
        assert response.status_code == 200
        row = next(r for r in response.json()["data"] if r["order_id"] == str(order.id))
        assert row["shiprocket_order_url"] == (
            "https://app.shiprocket.in/seller/orders/readytoship?order_ids=1576398335"
        )


async def test_shipment_queue_url_is_null_when_no_shipment_exists_yet(
    db_session: AsyncSession,
) -> None:
    ops = await _make_ops_user(db_session)
    customer = await make_customer(db_session)
    order = await make_order(
        db_session, order_number="LINK-002", customer=customer, status=OrderStatus.CONFIRMED
    )

    async with bearer_client(app, get_db, db_session, ops.id) as client:
        response = await client.get("/api/v1/shipments/queue")
        assert response.status_code == 200
        row = next(r for r in response.json()["data"] if r["order_id"] == str(order.id))
        assert row["shiprocket_order_url"] is None


async def test_shipment_queue_url_is_null_when_shipment_has_no_stored_order_id(
    db_session: AsyncSession,
) -> None:
    """A `Shipment` row that exists but was never created via this OMS's
    own push (e.g. pulled in from Shiprocket) -- no order id to read, so
    no link, never a guess.
    """
    ops = await _make_ops_user(db_session)
    customer = await make_customer(db_session)
    order = await make_order(
        db_session, order_number="LINK-003", customer=customer, status=OrderStatus.CONFIRMED
    )
    shipment = Shipment(
        order_id=order.id,
        current_status=ShipmentStatus.PENDING,
        source_system="shiprocket",
        external_id="9999",
        shiprocket_shipment_id="9999",
        raw_external_payload={"shipment_id": "9999"},
    )
    db_session.add(shipment)
    await db_session.commit()

    async with bearer_client(app, get_db, db_session, ops.id) as client:
        response = await client.get("/api/v1/shipments/queue")
        assert response.status_code == 200
        row = next(r for r in response.json()["data"] if r["order_id"] == str(order.id))
        assert row["shiprocket_order_url"] is None


# --- Exposed on Confirmed by Telecaller (`GET /orders?confirmed_only`) --


async def test_confirmed_by_telecaller_list_exposes_the_real_shiprocket_order_url(
    db_session: AsyncSession,
) -> None:
    """`OrderListResponse`/`_to_list_response` backs both plain `GET
    /orders` and `GET /orders?confirmed_only=true` (the "Confirmed by
    Telecaller" page) -- one response builder, exercised here through
    the plain list endpoint.
    """
    role = await make_role(db_session, name="OPERATIONS", permission_codes=["orders.read"])
    user = await make_user(db_session, email="ops-orders@shiprocket-link.example.com", role=role)
    customer = await make_customer(db_session)
    order = await make_order(
        db_session,
        order_number="LINK-004",
        customer=customer,
        status=OrderStatus.CONFIRMED,
    )
    shipment = Shipment(
        order_id=order.id,
        current_status=ShipmentStatus.PENDING,
        source_system="shiprocket",
        external_id="5002",
        shiprocket_shipment_id="5002",
        raw_external_payload={"order_id": "1576398400", "shipment_id": "5002"},
    )
    db_session.add(shipment)
    await db_session.commit()

    async with bearer_client(app, get_db, db_session, user.id) as client:
        response = await client.get("/api/v1/orders")
        assert response.status_code == 200
        rows = {r["id"]: r for r in response.json()["data"]}
        assert rows[str(order.id)]["shiprocket_order_url"] == (
            "https://app.shiprocket.in/seller/orders/readytoship?order_ids=1576398400"
        )


# --- No shipment-creation side effect from merely reading these rows ----


async def test_reading_the_queue_never_calls_shiprocket_or_shopify(
    db_session: AsyncSession,
) -> None:
    """Fetching the list that backs the Fulfillment Queue (what renders
    the "Process Shipment"/"Ship Order" buttons) must never itself talk
    to Shiprocket/Shopify -- it's a pure read of already-stored OMS data.
    No adapter is even registered here, so any attempted call would raise
    `IntegrationError`, which would surface as a 500 -- a 200 proves none
    was made.
    """
    ops = await _make_ops_user(db_session)
    customer = await make_customer(db_session)
    order = await make_order(
        db_session, order_number="LINK-005", customer=customer, status=OrderStatus.CONFIRMED
    )
    shipment = Shipment(
        order_id=order.id,
        current_status=ShipmentStatus.PENDING,
        source_system="shiprocket",
        external_id="5003",
        shiprocket_shipment_id="5003",
        raw_external_payload={"order_id": "1576398500", "shipment_id": "5003"},
    )
    db_session.add(shipment)
    await db_session.commit()

    async with bearer_client(app, get_db, db_session, ops.id) as client:
        response = await client.get("/api/v1/shipments/queue")
        assert response.status_code == 200

    await db_session.refresh(order)
    await db_session.refresh(shipment)
    assert order.fulfillment_status.value == "unfulfilled"
    assert shipment.current_status == ShipmentStatus.PENDING
    assert shipment.shopify_sync_status.value == "not_applicable"


# --- locate_shiprocket_orders() -- on-demand "find the EXISTING order" --


async def test_locate_resolves_instantly_from_an_existing_shipment_no_api_call(
    db_session: AsyncSession,
) -> None:
    """The fast path: a `Shipment` row already has a usable stored order
    id -- resolved with zero Shiprocket API calls (an empty stub client
    would raise `IndexError` if `request()` were ever called).
    """
    customer = await make_customer(db_session)
    order = await make_order(
        db_session, order_number="LOCATE-001", customer=customer, status=OrderStatus.CONFIRMED
    )
    shipment = Shipment(
        order_id=order.id,
        current_status=ShipmentStatus.PENDING,
        source_system="shiprocket",
        external_id="5010",
        shiprocket_shipment_id="5010",
        raw_external_payload={"order_id": "1600000001", "shipment_id": "5010"},
    )
    db_session.add(shipment)
    await db_session.commit()

    register_adapter(ShiprocketAdapter(client=_StubClient([])))

    results = await locate_shiprocket_orders(db_session, [order])
    assert results == {
        order.id: "https://app.shiprocket.in/seller/orders/readytoship?order_ids=1600000001"
    }


async def test_locate_finds_a_shipment_via_a_live_bounded_scan_and_persists_it(
    db_session: AsyncSession,
) -> None:
    """No local `Shipment` row yet -- reuses the EXACT same matching
    logic the periodic Shiprocket `shipments` sync uses
    (`entity_sync.ENTITY_UPSERT_HANDLERS["shipments"]`): a bounded,
    newest-first `/shipments` scan, falling back to `GET /orders/show/
    {id}` for `channel_order_id` (confirmed live to always be absent on
    `/shipments` itself). A genuine match is persisted as a real
    `Shipment` row -- "expose/store... for future use."
    """
    order = await _make_bare_order(db_session, order_number="#AWL77001")
    client = _StubClient(
        [
            _shipments_page(
                records=[
                    {
                        "id": 1700000001,
                        "channel_order_id": None,
                        "order_id": 1600000002,
                        "awb": "",
                        "status": "New",
                    }
                ]
            ),
            _orders_show_response("#AWL77001"),
        ]
    )
    register_adapter(ShiprocketAdapter(client=client))

    results = await locate_shiprocket_orders(db_session, [order])

    assert results == {
        order.id: "https://app.shiprocket.in/seller/orders/readytoship?order_ids=1600000002"
    }
    # Never a create-shipment call -- only the read/list endpoints.
    assert all(path != "/orders/create/adhoc" for _method, path, _json in client.calls)

    shipment = await ShipmentRepository(db_session).get_by_source_external_id(
        source_system="shiprocket", external_id="1700000001"
    )
    assert shipment is not None
    assert shipment.order_id == order.id


async def test_locate_reports_not_found_when_nothing_matches_never_guesses(
    db_session: AsyncSession,
) -> None:
    """A bounded scan that genuinely finds no match reports `None` --
    never a fabricated URL, and no `Shipment` row is created for this
    order (the unrelated candidate scanned belongs to a different,
    unknown order, so it's simply not persisted at all -- see
    `entity_sync._upsert_shipment`, which raises `NotFoundError` for an
    unresolved candidate).
    """
    order = await _make_bare_order(db_session, order_number="#AWL77002")
    client = _StubClient(
        [
            _shipments_page(
                records=[
                    {
                        "id": 1700000099,
                        "channel_order_id": None,
                        "order_id": 1600000099,
                        "awb": "",
                        "status": "New",
                    }
                ]
            ),
            _orders_show_response("#AWL-SOME-OTHER-ORDER"),
        ]
    )
    register_adapter(ShiprocketAdapter(client=client))

    order_id = order.id  # captured before the call -- a caught `IntegrationError`
    # inside `locate_shiprocket_orders` rolls back the session, which expires
    # every attribute on every object still attached to it (see
    # `SyncService._persist_sync_cursor`'s docstring for the same caution) --
    # a plain (non-awaited) attribute access afterward would raise
    # `MissingGreenlet` under `AsyncSession`.
    results = await locate_shiprocket_orders(db_session, [order])

    assert results == {order_id: None}
    assert all(path != "/orders/create/adhoc" for _method, path, _json in client.calls)


async def test_locate_never_creates_a_shipment_when_shiprocket_is_not_configured(
    db_session: AsyncSession,
) -> None:
    """No adapter registered at all -- resolves to `None` immediately,
    never raises, never guesses.
    """
    order = await _make_bare_order(db_session, order_number="#AWL77003")
    order_id = order.id  # captured before the call -- see the comment above.
    results = await locate_shiprocket_orders(db_session, [order])
    assert results == {order_id: None}


async def test_locate_resolves_multiple_orders_from_one_shared_scan(
    db_session: AsyncSession,
) -> None:
    """Bulk: a single `/shipments` page can resolve more than one of the
    selected orders -- one shared scan, not one scan per order.
    """
    order_a = await _make_bare_order(db_session, order_number="#AWL77010")
    order_b = await _make_bare_order(db_session, order_number="#AWL77011")
    client = _StubClient(
        [
            _shipments_page(
                records=[
                    {
                        "id": 1700000010,
                        "channel_order_id": None,
                        "order_id": 1600000010,
                        "awb": "",
                    },
                    {
                        "id": 1700000011,
                        "channel_order_id": None,
                        "order_id": 1600000011,
                        "awb": "",
                    },
                ]
            ),
            _orders_show_response("#AWL77010"),
            _orders_show_response("#AWL77011"),
        ]
    )
    register_adapter(ShiprocketAdapter(client=client))

    results = await locate_shiprocket_orders(db_session, [order_a, order_b])

    assert results == {
        order_a.id: "https://app.shiprocket.in/seller/orders/readytoship?order_ids=1600000010",
        order_b.id: "https://app.shiprocket.in/seller/orders/readytoship?order_ids=1600000011",
    }


# --- POST /shipments/locate-shiprocket-order -----------------------------


async def test_locate_endpoint_requires_shipments_update_permission(
    db_session: AsyncSession,
) -> None:
    role = await make_role(db_session, name="READ_ONLY_OPS", permission_codes=["shipments.read"])
    user = await make_user(db_session, email="readonly@shiprocket-link.example.com", role=role)
    customer = await make_customer(db_session)
    order = await make_order(
        db_session, order_number="LOCATE-RBAC-1", customer=customer, status=OrderStatus.CONFIRMED
    )

    async with bearer_client(app, get_db, db_session, user.id) as client:
        response = await client.post(
            "/api/v1/shipments/locate-shiprocket-order", json={"order_ids": [str(order.id)]}
        )
        assert response.status_code == 403


async def test_locate_endpoint_returns_found_and_not_found_per_order(
    db_session: AsyncSession,
) -> None:
    ops = await _make_ops_user(db_session)
    customer = await make_customer(db_session)
    found_order = await make_order(
        db_session, order_number="#AWL77020", customer=customer, status=OrderStatus.CONFIRMED
    )
    existing_shipment = Shipment(
        order_id=found_order.id,
        current_status=ShipmentStatus.PENDING,
        source_system="shiprocket",
        external_id="5020",
        shiprocket_shipment_id="5020",
        raw_external_payload={"order_id": "1600000020", "shipment_id": "5020"},
    )
    db_session.add(existing_shipment)
    unresolvable_order = await make_order(
        db_session, order_number="#AWL77021", customer=customer, status=OrderStatus.CONFIRMED
    )
    await db_session.commit()

    register_adapter(ShiprocketAdapter(client=_StubClient([_shipments_page(records=[])])))

    async with bearer_client(app, get_db, db_session, ops.id) as client:
        response = await client.post(
            "/api/v1/shipments/locate-shiprocket-order",
            json={"order_ids": [str(found_order.id), str(unresolvable_order.id)]},
        )
        assert response.status_code == 200
        results = {r["order_id"]: r for r in response.json()["data"]}
        assert results[str(found_order.id)]["status"] == "found"
        assert results[str(found_order.id)]["shiprocket_order_url"] == (
            "https://app.shiprocket.in/seller/orders/readytoship?order_ids=1600000020"
        )
        assert results[str(unresolvable_order.id)]["status"] == "not_found"
        assert results[str(unresolvable_order.id)]["shiprocket_order_url"] is None
        assert results[str(unresolvable_order.id)]["message"]


async def test_locate_endpoint_never_calls_create_shipment(db_session: AsyncSession) -> None:
    ops = await _make_ops_user(db_session)
    customer = await make_customer(db_session)
    order = await make_order(
        db_session,
        order_number="LOCATE-NOCREATE-1",
        customer=customer,
        status=OrderStatus.CONFIRMED,
    )
    client = _StubClient([_shipments_page(records=[])])
    register_adapter(ShiprocketAdapter(client=client))

    async with bearer_client(app, get_db, db_session, ops.id) as api_client:
        response = await api_client.post(
            "/api/v1/shipments/locate-shiprocket-order", json={"order_ids": [str(order.id)]}
        )
        assert response.status_code == 200

    assert all(path != "/orders/create/adhoc" for _method, path, _json in client.calls)


# --- POST /shipment-staff/orders/locate-shiprocket-order (scoped) --------


async def test_shipment_staff_locate_endpoint_rejects_an_order_outside_scope(
    db_session: AsyncSession,
) -> None:
    shipment_staff_role = await make_role(
        db_session, name="SHIPMENT_STAFF_LINK", permission_codes=["shipment_staff.manage"]
    )
    shipment_staff = await make_user(
        db_session, email="ss-link@shiprocket-link.example.com", role=shipment_staff_role
    )
    # A Telecaller NOT assigned to `shipment_staff` (no `shipment_staff_id`
    # set) -- the order below is confirmed by them, so it's outside this
    # Shipment Staff user's scope (`ShipmentStaffService.resolve_scope`).
    telecaller_role = await make_role(
        db_session, name="TELECALLER_LINK", permission_codes=["calls.manage", "orders.confirm"]
    )
    other_telecaller = await make_user(
        db_session, email="other-tc@shiprocket-link.example.com", role=telecaller_role
    )
    customer = await make_customer(db_session)
    order = await make_order(
        db_session, order_number="LOCATE-SCOPE-1", customer=customer, status=OrderStatus.CONFIRMED
    )
    order.confirmed_by_telecaller_id = other_telecaller.id
    await db_session.commit()

    async with bearer_client(app, get_db, db_session, shipment_staff.id) as client:
        response = await client.post(
            "/api/v1/shipment-staff/orders/locate-shiprocket-order",
            json={"order_ids": [str(order.id)]},
        )
        assert response.status_code == 200
        results = {r["order_id"]: r for r in response.json()["data"]}
        assert results[str(order.id)]["status"] == "error"

"""OMS -> Shopify outbound fulfillment push (Phase 6).

Every scenario here exercises `ShopifyFulfillmentService` directly (unit
level) plus one end-to-end check that `ShiprocketOperationsService.
assign_awb` actually triggers it, and one RBAC check on the manual retry
endpoint. No real Shopify account -- `_StubShopifyClient` returns
hand-built GraphQL response shapes matching
`app.integrations.shopify.mutations`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from app.db.session import get_db
from app.integrations.registry import clear_adapters, register_adapter
from app.integrations.shopify.adapter import ShopifyAdapter
from app.main import app
from app.models.customer import Customer
from app.models.enums import PaymentStatus, PaymentType, ShopifySyncStatus
from app.models.order import Order
from app.models.shipment import Shipment
from app.services.shopify_fulfillment_service import ShopifyFulfillmentService
from sqlalchemy.ext.asyncio import AsyncSession

from tests.telecalling_test_utils import bearer_client, make_role, make_user

pytestmark = pytest.mark.asyncio


class _StubShopifyClient:
    def __init__(self, responses: list) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[str, dict | None]] = []

    async def execute(self, query: str, variables: dict | None = None) -> dict:
        self.calls.append((query, variables))
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


@pytest.fixture(autouse=True)
def _reset_registry():
    yield
    clear_adapters()


async def _make_order(
    session: AsyncSession, *, order_number: str, shopify_order_id: str | None = "900001"
) -> Order:
    customer = Customer(full_name="Test Customer", phone="9999999999", email="c@example.com")
    session.add(customer)
    await session.flush()
    order = Order(
        order_number=order_number,
        customer_id=customer.id,
        shopify_order_id=shopify_order_id,
        order_datetime=datetime.now(UTC),
        currency="INR",
        subtotal=Decimal("499.00"),
        total_amount=Decimal("499.00"),
        payment_type=PaymentType.PREPAID,
        payment_status=PaymentStatus.PAID,
    )
    session.add(order)
    await session.flush()
    return order


async def _make_shipment(
    session: AsyncSession, *, order_id, awb: str | None = "AWB123"
) -> Shipment:
    shipment = Shipment(order_id=order_id, awb=awb, source_system="shiprocket")
    session.add(shipment)
    await session.commit()
    await session.refresh(shipment)
    return shipment


def _open_fulfillment_orders_response(fulfillment_order_id: str | None) -> dict:
    edges = (
        [{"node": {"id": fulfillment_order_id, "status": "OPEN"}}] if fulfillment_order_id else []
    )
    return {"order": {"id": "gid://shopify/Order/900001", "fulfillmentOrders": {"edges": edges}}}


def _fulfillment_create_success(fulfillment_id: str = "gid://shopify/Fulfillment/5001") -> dict:
    return {
        "fulfillmentCreate": {
            "fulfillment": {"id": fulfillment_id, "status": "SUCCESS", "trackingInfo": {}},
            "userErrors": [],
        }
    }


def _fulfillment_create_user_error() -> dict:
    return {
        "fulfillmentCreate": {
            "fulfillment": None,
            "userErrors": [{"field": ["fulfillment"], "message": "Order is already fulfilled."}],
        }
    }


async def test_manual_order_shipment_is_not_applicable(db_session: AsyncSession) -> None:
    """An order created directly in the OMS (no `shopify_order_id`) is
    never eligible for the Shopify push -- no adapter call is even made.
    """
    order = await _make_order(db_session, order_number="SHOPIFY-MANUAL-1", shopify_order_id=None)
    shipment = await _make_shipment(db_session, order_id=order.id)

    result = await ShopifyFulfillmentService(db_session).sync_fulfillment_for_shipment(
        shipment.id, actor=None
    )
    assert result.shopify_sync_status == ShopifySyncStatus.NOT_APPLICABLE
    assert result.shopify_fulfillment_id is None


async def test_shipment_with_no_awb_yet_stays_pending(db_session: AsyncSession) -> None:
    order = await _make_order(db_session, order_number="SHOPIFY-NOAWB-1")
    shipment = await _make_shipment(db_session, order_id=order.id, awb=None)
    # Mirrors what create_shipment_for_order does on creation.
    from app.repositories.shipment import ShipmentRepository

    await ShipmentRepository(db_session).update(
        shipment, shopify_sync_status=ShopifySyncStatus.PENDING
    )
    await db_session.commit()

    result = await ShopifyFulfillmentService(db_session).sync_fulfillment_for_shipment(
        shipment.id, actor=None
    )
    assert result.shopify_sync_status == ShopifySyncStatus.PENDING
    assert result.shopify_fulfillment_id is None


async def test_sync_succeeds_and_stores_fulfillment_id(db_session: AsyncSession) -> None:
    order = await _make_order(db_session, order_number="SHOPIFY-OK-1")
    shipment = await _make_shipment(db_session, order_id=order.id)
    client = _StubShopifyClient(
        [_open_fulfillment_orders_response("gid://shopify/FulfillmentOrder/1"),
         _fulfillment_create_success()]
    )
    register_adapter(ShopifyAdapter(client=client))

    result = await ShopifyFulfillmentService(db_session).sync_fulfillment_for_shipment(
        shipment.id, actor=None
    )
    assert result.shopify_sync_status == ShopifySyncStatus.SYNCED
    assert result.shopify_fulfillment_id == "gid://shopify/Fulfillment/5001"
    assert result.shopify_sync_error is None
    assert result.shopify_synced_at is not None


async def test_sync_failure_does_not_corrupt_shipment_state(db_session: AsyncSession) -> None:
    """A Shopify-side rejection (userErrors) must never touch the
    shipment's real logistics fields (AWB, current_status) -- only the
    shopify_sync_* columns change.
    """
    order = await _make_order(db_session, order_number="SHOPIFY-FAIL-1")
    shipment = await _make_shipment(db_session, order_id=order.id)
    original_status = shipment.current_status
    client = _StubShopifyClient(
        [_open_fulfillment_orders_response("gid://shopify/FulfillmentOrder/1"),
         _fulfillment_create_user_error()]
    )
    register_adapter(ShopifyAdapter(client=client))

    result = await ShopifyFulfillmentService(db_session).sync_fulfillment_for_shipment(
        shipment.id, actor=None
    )
    assert result.shopify_sync_status == ShopifySyncStatus.FAILED
    assert result.shopify_fulfillment_id is None
    assert "already fulfilled" in (result.shopify_sync_error or "")
    assert result.current_status == original_status
    assert result.awb == "AWB123"


async def test_already_synced_shipment_is_never_pushed_twice(db_session: AsyncSession) -> None:
    order = await _make_order(db_session, order_number="SHOPIFY-IDEMPOTENT-1")
    shipment = await _make_shipment(db_session, order_id=order.id)
    client = _StubShopifyClient(
        [_open_fulfillment_orders_response("gid://shopify/FulfillmentOrder/1"),
         _fulfillment_create_success()]
    )
    register_adapter(ShopifyAdapter(client=client))
    service = ShopifyFulfillmentService(db_session)

    first = await service.sync_fulfillment_for_shipment(shipment.id, actor=None)
    assert first.shopify_sync_status == ShopifySyncStatus.SYNCED
    calls_after_first = len(client.calls)

    second = await service.sync_fulfillment_for_shipment(shipment.id, actor=None)
    assert second.shopify_fulfillment_id == first.shopify_fulfillment_id
    # No new GraphQL call was made -- the idempotency guard short-circuits
    # before ever touching the adapter again.
    assert len(client.calls) == calls_after_first


async def test_retry_after_failure_recovers_without_duplicating(db_session: AsyncSession) -> None:
    """If a prior attempt was marked FAILED but Shopify actually has no
    remaining open fulfillment order for the order (e.g. the earlier
    attempt secretly succeeded upstream despite a lost OMS response), a
    retry must resolve to SYNCED rather than loop on FAILED forever or
    call fulfillmentCreate a second time.
    """
    order = await _make_order(db_session, order_number="SHOPIFY-RETRY-1")
    shipment = await _make_shipment(db_session, order_id=order.id)
    service = ShopifyFulfillmentService(db_session)

    from app.integrations.shopify.errors import ShopifyApiError

    failing_client = _StubShopifyClient(
        [ShopifyApiError("Shopify request failed.", error_type="http_500")]
    )
    register_adapter(ShopifyAdapter(client=failing_client))
    first = await service.sync_fulfillment_for_shipment(shipment.id, actor=None)
    assert first.shopify_sync_status == ShopifySyncStatus.FAILED

    recovering_client = _StubShopifyClient([_open_fulfillment_orders_response(None)])
    register_adapter(ShopifyAdapter(client=recovering_client))
    second = await service.sync_fulfillment_for_shipment(shipment.id, actor=None)

    assert second.shopify_sync_status == ShopifySyncStatus.SYNCED
    assert second.shopify_fulfillment_id is None
    # Only the fulfillment-orders lookup ran -- fulfillmentCreate was
    # never called, since there was nothing left to fulfill.
    assert len(recovering_client.calls) == 1


async def test_assign_awb_triggers_the_shopify_sync(db_session: AsyncSession) -> None:
    """End-to-end: assigning an AWB via Shiprocket automatically triggers
    the Shopify push for a Shopify-sourced order -- the actual production
    trigger point, not a direct service call.
    """
    from app.core.config import settings
    from app.integrations.shiprocket.adapter import ShiprocketAdapter
    from app.services.shiprocket_service import ShiprocketOperationsService

    monkey = pytest.MonkeyPatch()
    monkey.setattr(settings, "SHIPROCKET_EMAIL", "ops@example.com")
    monkey.setattr(settings, "SHIPROCKET_PASSWORD", "secret")
    monkey.setattr(settings, "SHIPROCKET_PICKUP_LOCATION", "Main Warehouse")

    order = await _make_order(db_session, order_number="SHOPIFY-E2E-1")

    class _StubShiprocketClient:
        def __init__(self, responses):
            self._responses = list(responses)

        async def request(self, method, path, *, json=None, params=None):
            return self._responses.pop(0)

        async def ensure_authenticated(self):
            pass

    register_adapter(
        ShiprocketAdapter(
            client=_StubShiprocketClient(
                [
                    {"order_id": "9001", "shipment_id": "5001", "status": "NEW"},
                    {
                        "response": {
                            "data": {
                                "awb_code": "AWB999",
                                "courier_name": "Delhivery",
                                "courier_company_id": "51",
                            }
                        }
                    },
                ]
            )
        )
    )
    register_adapter(
        ShopifyAdapter(
            client=_StubShopifyClient(
                [
                    _open_fulfillment_orders_response("gid://shopify/FulfillmentOrder/1"),
                    _fulfillment_create_success(),
                ]
            )
        )
    )

    ops = ShiprocketOperationsService(db_session)
    shipment = await ops.create_shipment_for_order(order.id, actor=None)
    assert shipment.shopify_sync_status == ShopifySyncStatus.PENDING

    shipment = await ops.assign_awb(shipment.id, actor=None, courier_id=None)
    assert shipment.awb == "AWB999"
    assert shipment.shopify_sync_status == ShopifySyncStatus.SYNCED
    assert shipment.shopify_fulfillment_id == "gid://shopify/Fulfillment/5001"

    monkey.undo()


async def test_telecaller_cannot_retry_shopify_sync(db_session: AsyncSession) -> None:
    role = await make_role(
        db_session, name="TELECALLER", permission_codes=["calls.manage", "orders.confirm"]
    )
    user = await make_user(db_session, email="tc-shopify@example.com", role=role)
    order = await _make_order(db_session, order_number="SHOPIFY-RBAC-1")
    shipment = await _make_shipment(db_session, order_id=order.id)

    async with bearer_client(app, get_db, db_session, user.id) as client:
        response = await client.post(f"/api/v1/shipments/{shipment.id}/shopify/retry-sync")
        assert response.status_code == 403
    app.dependency_overrides.clear()


async def test_fulfillment_role_can_retry_shopify_sync(db_session: AsyncSession) -> None:
    role = await make_role(
        db_session, name="FULFILLMENT", permission_codes=["shipments.read", "shipments.update"]
    )
    user = await make_user(db_session, email="fulfil-shopify@example.com", role=role)
    order = await _make_order(db_session, order_number="SHOPIFY-RBAC-2")
    shipment = await _make_shipment(db_session, order_id=order.id)
    client = _StubShopifyClient(
        [_open_fulfillment_orders_response("gid://shopify/FulfillmentOrder/1"),
         _fulfillment_create_success()]
    )
    register_adapter(ShopifyAdapter(client=client))

    async with bearer_client(app, get_db, db_session, user.id) as api_client:
        response = await api_client.post(f"/api/v1/shipments/{shipment.id}/shopify/retry-sync")
        assert response.status_code == 200
        assert response.json()["data"]["shopify_sync_status"] == "synced"
    app.dependency_overrides.clear()

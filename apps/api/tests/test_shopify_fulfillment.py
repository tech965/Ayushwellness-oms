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
from app.models.enums import OrderStatus, PaymentStatus, PaymentType, ShopifySyncStatus
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
    session: AsyncSession,
    *,
    order_number: str,
    shopify_order_id: str | None = "900001",
    shopify_confirmation_fulfillment_id: str | None = None,
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
        # CONFIRMED + a shipping address -- the minimum
        # `ShiprocketOperationsService._check_shippable` requires, needed
        # by this file's one `create_shipment_for_order` E2E case; every
        # other test here exercises `ShopifyFulfillmentService` directly
        # via `_make_shipment` and never touches order state, so this is
        # harmless for those.
        status=OrderStatus.CONFIRMED,
        shipping_address={
            "line1": "123 Test St",
            "city": "Mumbai",
            "state": "MH",
            "country": "India",
            "pin_code": "400001",
        },
        # Simulates an order Telecaller confirmation already pushed to
        # Shopify as a Fulfillment (`sync_confirmation_fulfillment`) --
        # used by the `sync_fulfillment_for_shipment` fallback tests
        # below, where real shipping needs to attach tracking to that
        # SAME Fulfillment instead of creating a second one.
        shopify_confirmation_fulfillment_id=shopify_confirmation_fulfillment_id,
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


def _tags_add_success_response(order_gid: str = "gid://shopify/Order/900001") -> dict:
    return {"tagsAdd": {"node": {"id": order_gid}, "userErrors": []}}


def _tags_remove_success_response(order_gid: str = "gid://shopify/Order/900001") -> dict:
    return {"tagsRemove": {"node": {"id": order_gid}, "userErrors": []}}


def _fulfillment_cancel_success_response(
    fulfillment_id: str = "gid://shopify/Fulfillment/5001",
) -> dict:
    return {
        "fulfillmentCancel": {
            "fulfillment": {"id": fulfillment_id, "status": "CANCELLED"},
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


# ----------------------------------------------------------------------
# Telecaller-confirmation fulfillment push/reversal
# (`sync_confirmation_fulfillment` / `reverse_confirmation_fulfillment`)
# and the `sync_fulfillment_for_shipment` fallback for when confirmation
# already closed the order's one FulfillmentOrder.
# ----------------------------------------------------------------------


async def test_sync_confirmation_fulfillment_creates_a_fulfillment_with_no_tracking(
    db_session: AsyncSession,
) -> None:
    order = await _make_order(db_session, order_number="CONF-FULFILL-OK-1")
    client = _StubShopifyClient(
        [_open_fulfillment_orders_response("gid://shopify/FulfillmentOrder/1"),
         _fulfillment_create_success("gid://shopify/Fulfillment/9001")]
    )
    register_adapter(ShopifyAdapter(client=client))

    result = await ShopifyFulfillmentService(db_session).sync_confirmation_fulfillment(
        order.id, actor=None
    )
    assert result.shopify_confirmation_sync_status == ShopifySyncStatus.SYNCED
    assert result.shopify_confirmation_fulfillment_id == "gid://shopify/Fulfillment/9001"

    # No tracking info was sent -- Telecaller confirmation carries no
    # AWB/courier, unlike the real shipping push.
    _query, variables = client.calls[1]
    assert variables["fulfillment"]["lineItemsByFulfillmentOrder"] == [
        {"fulfillmentOrderId": "gid://shopify/FulfillmentOrder/1"}
    ]
    assert "trackingInfo" not in variables["fulfillment"]


async def test_sync_confirmation_fulfillment_is_never_pushed_twice(
    db_session: AsyncSession,
) -> None:
    order = await _make_order(db_session, order_number="CONF-FULFILL-IDEMPOTENT-1")
    client = _StubShopifyClient(
        [_open_fulfillment_orders_response("gid://shopify/FulfillmentOrder/1"),
         _fulfillment_create_success("gid://shopify/Fulfillment/9002")]
    )
    register_adapter(ShopifyAdapter(client=client))
    service = ShopifyFulfillmentService(db_session)

    first = await service.sync_confirmation_fulfillment(order.id, actor=None)
    assert first.shopify_confirmation_sync_status == ShopifySyncStatus.SYNCED
    calls_after_first = len(client.calls)

    second = await service.sync_confirmation_fulfillment(order.id, actor=None)
    assert second.shopify_confirmation_fulfillment_id == first.shopify_confirmation_fulfillment_id
    assert len(client.calls) == calls_after_first


async def test_sync_confirmation_fulfillment_failure_is_retryable_and_never_duplicates(
    db_session: AsyncSession,
) -> None:
    from app.integrations.shopify.errors import ShopifyApiError

    order = await _make_order(db_session, order_number="CONF-FULFILL-RETRY-1")
    service = ShopifyFulfillmentService(db_session)

    failing_client = _StubShopifyClient(
        [ShopifyApiError("Shopify request failed.", error_type="http_500")]
    )
    register_adapter(ShopifyAdapter(client=failing_client))
    first = await service.sync_confirmation_fulfillment(order.id, actor=None)
    assert first.shopify_confirmation_sync_status == ShopifySyncStatus.FAILED
    assert first.shopify_confirmation_fulfillment_id is None

    recovering_client = _StubShopifyClient(
        [_open_fulfillment_orders_response("gid://shopify/FulfillmentOrder/1"),
         _fulfillment_create_success("gid://shopify/Fulfillment/9003")]
    )
    register_adapter(ShopifyAdapter(client=recovering_client))
    second = await service.sync_confirmation_fulfillment(order.id, actor=None)
    assert second.shopify_confirmation_sync_status == ShopifySyncStatus.SYNCED
    assert second.shopify_confirmation_fulfillment_id == "gid://shopify/Fulfillment/9003"


async def test_reverse_confirmation_fulfillment_is_a_noop_when_nothing_to_reverse(
    db_session: AsyncSession,
) -> None:
    """No `shopify_confirmation_fulfillment_id` set (never synced, or a
    previous reversal already succeeded) -- the tag removal still fires
    (best-effort, harmless), but `fulfillmentCancel` is never called.
    """
    order = await _make_order(db_session, order_number="CONF-REVERSE-NOOP-1")
    client = _StubShopifyClient([_tags_remove_success_response()])
    register_adapter(ShopifyAdapter(client=client))

    result = await ShopifyFulfillmentService(db_session).reverse_confirmation_fulfillment(
        order.id, actor=None
    )
    assert result.shopify_confirmation_fulfillment_id is None
    assert len(client.calls) == 1
    assert "tagsRemove" in client.calls[0][0]


async def test_reverse_confirmation_fulfillment_cancels_exactly_that_fulfillment(
    db_session: AsyncSession,
) -> None:
    order = await _make_order(
        db_session,
        order_number="CONF-REVERSE-OK-1",
        shopify_confirmation_fulfillment_id="gid://shopify/Fulfillment/9004",
    )
    client = _StubShopifyClient(
        [_tags_remove_success_response(), _fulfillment_cancel_success_response()]
    )
    register_adapter(ShopifyAdapter(client=client))

    result = await ShopifyFulfillmentService(db_session).reverse_confirmation_fulfillment(
        order.id, actor=None
    )
    assert result.shopify_confirmation_fulfillment_id is None
    assert result.shopify_confirmation_sync_status == ShopifySyncStatus.NOT_APPLICABLE

    cancel_query, cancel_variables = client.calls[1]
    assert "fulfillmentCancel" in cancel_query
    assert cancel_variables == {"id": "gid://shopify/Fulfillment/9004"}


async def test_reverse_confirmation_fulfillment_raises_and_preserves_state_on_failure(
    db_session: AsyncSession,
) -> None:
    """Unlike every other method here, a cancellation failure DOES raise
    -- `OrderService.unconfirm_order` depends on that to abort the whole
    revert. The failure is recorded (FAILED, retryable) and the
    Fulfillment id is left in place -- there is still something to retry
    cancelling.
    """
    from app.core.exceptions import IntegrationError

    order = await _make_order(
        db_session,
        order_number="CONF-REVERSE-FAIL-1",
        shopify_confirmation_fulfillment_id="gid://shopify/Fulfillment/9005",
    )
    client = _StubShopifyClient(
        [
            _tags_remove_success_response(),
            IntegrationError("Shopify rejected the cancellation.", details={}),
        ]
    )
    register_adapter(ShopifyAdapter(client=client))

    with pytest.raises(IntegrationError):
        await ShopifyFulfillmentService(db_session).reverse_confirmation_fulfillment(
            order.id, actor=None
        )

    await db_session.refresh(order)
    assert order.shopify_confirmation_fulfillment_id == "gid://shopify/Fulfillment/9005"
    assert order.shopify_confirmation_sync_status == ShopifySyncStatus.FAILED


async def test_awb_assignment_attaches_tracking_to_the_confirmation_fulfillment(
    db_session: AsyncSession,
) -> None:
    """The critical interaction test: when Telecaller confirmation already
    closed the order's one FulfillmentOrder, real shipping (AWB
    assignment) must not silently no-op -- it attaches tracking to that
    SAME Fulfillment via `fulfillmentTrackingInfoUpdate`, never a second
    `fulfillmentCreate` (there's nothing left to create).
    """
    order = await _make_order(
        db_session,
        order_number="CONF-TRACKING-FALLBACK-1",
        shopify_confirmation_fulfillment_id="gid://shopify/Fulfillment/9006",
    )
    shipment = await _make_shipment(db_session, order_id=order.id, awb="AWB777")
    client = _StubShopifyClient(
        [
            _open_fulfillment_orders_response(None),  # closed -- nothing OPEN left
            {
                "fulfillmentTrackingInfoUpdate": {
                    "fulfillment": {
                        "id": "gid://shopify/Fulfillment/9006",
                        "status": "SUCCESS",
                        "trackingInfo": {"number": "AWB777", "company": None, "url": None},
                    },
                    "userErrors": [],
                }
            },
        ]
    )
    register_adapter(ShopifyAdapter(client=client))

    result = await ShopifyFulfillmentService(db_session).sync_fulfillment_for_shipment(
        shipment.id, actor=None
    )
    assert result.shopify_sync_status == ShopifySyncStatus.SYNCED
    assert result.shopify_fulfillment_id == "gid://shopify/Fulfillment/9006"

    update_query, update_variables = client.calls[1]
    assert "fulfillmentTrackingInfoUpdate" in update_query
    assert update_variables["fulfillmentId"] == "gid://shopify/Fulfillment/9006"
    assert update_variables["trackingInfoInput"]["number"] == "AWB777"


async def test_telecaller_cannot_retry_shopify_confirmation_sync_via_orders_endpoint(
    db_session: AsyncSession,
) -> None:
    role = await make_role(
        db_session, name="TELECALLER", permission_codes=["calls.manage", "orders.confirm"]
    )
    user = await make_user(db_session, email="tc-confirm-sync@example.com", role=role)
    order = await _make_order(db_session, order_number="SHOPIFY-RBAC-3")

    async with bearer_client(app, get_db, db_session, user.id) as client:
        response = await client.post(f"/api/v1/orders/{order.id}/shopify/retry-confirmation-sync")
        assert response.status_code == 403
    app.dependency_overrides.clear()


async def test_operations_role_can_retry_shopify_confirmation_sync(
    db_session: AsyncSession,
) -> None:
    """The retry endpoint only re-pushes `CONFIRMATION_TAG` now -- never
    `fulfillmentCreate` -- so the stub only ever needs to answer one call.
    """
    role = await make_role(db_session, name="OPERATIONS", permission_codes=["orders.update"])
    user = await make_user(db_session, email="ops-confirm-sync@example.com", role=role)
    order = await _make_order(db_session, order_number="SHOPIFY-RBAC-4")
    client = _StubShopifyClient([_tags_add_success_response()])
    register_adapter(ShopifyAdapter(client=client))

    async with bearer_client(app, get_db, db_session, user.id) as api_client:
        response = await api_client.post(
            f"/api/v1/orders/{order.id}/shopify/retry-confirmation-sync"
        )
        assert response.status_code == 200

    assert len(client.calls) == 1
    query, variables = client.calls[0]
    assert "tagsAdd" in query
    assert variables == {"id": "gid://shopify/Order/900001", "tags": ["OMS Confirmed"]}
    app.dependency_overrides.clear()

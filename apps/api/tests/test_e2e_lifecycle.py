"""Full chained lifecycle — Phase 2.4 spec §3/§4/§5/§10.

SHOPIFY -> OMS ORDER -> SHIPROCKET -> SHIPMENT -> AWB -> COURIER ->
TRACKING -> DELIVERED, exercised as one continuous flow through the real
services (`SyncService`, `ShiprocketOperationsService`,
`app.integrations.shiprocket.sync.refresh_tracking`) rather than unit
tests of each step in isolation — those already exist per-provider in
`test_shopify_sync.py`/`test_shiprocket_sync.py`/`test_shiprocket_operations.py`.
This file's job is specifically to prove the *chain* holds together and
that data ownership boundaries (spec §10) survive every step. No real
provider account; both adapters are stubbed at the HTTP boundary.
"""

from __future__ import annotations

import pytest
from app.core.config import settings
from app.integrations.registry import (
    clear_adapters,
    register_adapter,
    restore_adapters,
    snapshot_adapters,
)
from app.integrations.shiprocket.adapter import ShiprocketAdapter
from app.integrations.shopify.adapter import ShopifyAdapter
from app.models.enums import IntegrationStatus, IntegrationType, ShipmentStatus, SyncType
from app.models.integration import Integration, IntegrationCode
from app.models.order import Order
from app.repositories.audit_log import AuditLogRepository
from app.repositories.integration import IntegrationRepository
from app.repositories.order import OrderRepository
from app.repositories.shipment import ShipmentRepository
from app.services.shiprocket_service import ShiprocketOperationsService
from app.services.sync_service import SyncService
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio


class _StubShopifyClient:
    def __init__(self, response: dict) -> None:
        self._response = response

    async def execute(self, query: str, variables: dict | None = None) -> dict:
        return self._response


class _StubShiprocketClient:
    def __init__(self, responses: list) -> None:
        self._responses = list(responses)

    async def request(
        self, method: str, path: str, *, json: dict | None = None, params: dict | None = None
    ) -> dict:
        return self._responses.pop(0)

    async def ensure_authenticated(self) -> None:
        pass


@pytest.fixture(autouse=True)
def _reset_adapter_registry():
    snapshot = snapshot_adapters()
    clear_adapters()
    yield
    restore_adapters(snapshot)


@pytest.fixture(autouse=True)
def _configure_shiprocket(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "SHIPROCKET_EMAIL", "ops@example.com")
    monkeypatch.setattr(settings, "SHIPROCKET_PASSWORD", "super-secret-password")
    monkeypatch.setattr(settings, "SHIPROCKET_PICKUP_LOCATION", "Main Warehouse")


def _shopify_order_page(order_id: str = "8001") -> dict:
    return {
        "orders": {
            "pageInfo": {"hasNextPage": False, "endCursor": None},
            "edges": [
                {
                    "node": {
                        "id": f"gid://shopify/Order/{order_id}",
                        "name": f"#{order_id}",
                        "createdAt": "2026-01-01T00:00:00Z",
                        "updatedAt": "2026-01-02T00:00:00Z",
                        "cancelledAt": None,
                        "currencyCode": "INR",
                        "displayFinancialStatus": "PAID",
                        "displayFulfillmentStatus": "UNFULFILLED",
                        "subtotalPriceSet": {"shopMoney": {"amount": "999.00"}},
                        "totalDiscountsSet": {"shopMoney": {"amount": "0.00"}},
                        "totalTaxSet": {"shopMoney": {"amount": "0.00"}},
                        "totalPriceSet": {"shopMoney": {"amount": "999.00"}},
                        "shippingLine": None,
                        "paymentGatewayNames": ["Razorpay"],
                        "customer": None,
                        "shippingAddress": {
                            "name": "Jane Doe",
                            "address1": "221B Baker Street",
                            "address2": None,
                            "city": "Mumbai",
                            "province": "Maharashtra",
                            "country": "India",
                            "zip": "400001",
                            "phone": "9876543210",
                        },
                        "billingAddress": None,
                        "lineItems": {
                            "edges": [
                                {
                                    "node": {
                                        "id": "gid://shopify/LineItem/1",
                                        "sku": "ASH-60",
                                        "title": "Ashwagandha",
                                        "quantity": 1,
                                        "originalUnitPriceSet": {"shopMoney": {"amount": "999.00"}},
                                        "discountedTotalSet": {"shopMoney": {"amount": "999.00"}},
                                        "variant": None,
                                    }
                                }
                            ]
                        },
                    }
                }
            ],
        }
    }


def _create_order_response() -> dict:
    return {"order_id": "9001", "shipment_id": "5001", "status": "NEW"}


def _assign_awb_response() -> dict:
    return {
        "response": {
            "data": {"awb_code": "AWB777", "courier_name": "Delhivery", "courier_company_id": "51"}
        }
    }


def _tracking_response(status: str) -> dict:
    return {
        "tracking_data": {
            "awb": "AWB777",
            "shipment_track_activities": [
                {
                    "id": 1,
                    "status": status,
                    "date": "2026-01-05 10:00:00",
                    "activity": status,
                    "courier_name": "Delhivery",
                }
            ],
        }
    }


async def _sync_one_shopify_order(session: AsyncSession) -> Order:
    integration = await IntegrationRepository(session).create(
        name="Shopify",
        code=IntegrationCode.SHOPIFY,
        type=IntegrationType.ECOMMERCE,
        status=IntegrationStatus.DISCONNECTED,
        enabled=True,
    )
    await session.commit()

    register_adapter(ShopifyAdapter(client=_StubShopifyClient(_shopify_order_page())))
    job = await SyncService(session).run_sync(
        integration_id=integration.id, sync_type=SyncType.FULL, entity_type="orders"
    )
    assert job.status.value == "completed"

    order = await OrderRepository(session).get_by_source_external_id(
        source_system="shopify", external_id="8001"
    )
    assert order is not None
    return order


# Full chain: SHOPIFY -> OMS ORDER -> SHIPROCKET -> SHIPMENT -> AWB ->
# COURIER -> TRACKING -> DELIVERED
async def test_full_shopify_to_shiprocket_delivery_lifecycle(db_session: AsyncSession) -> None:
    order = await _sync_one_shopify_order(db_session)
    order_id = order.id
    shopify_order_id = order.shopify_order_id
    total_amount = order.total_amount

    # OMS ORDER -> SHIPROCKET -> SHIPMENT
    register_adapter(ShiprocketAdapter(client=_StubShiprocketClient([_create_order_response()])))
    ops = ShiprocketOperationsService(db_session)
    shipment = await ops.create_shipment_for_order(order_id, actor=None)
    assert shipment.shiprocket_shipment_id == "5001"
    assert shipment.order_id == order_id

    # SHIPMENT -> AWB -> COURIER
    register_adapter(ShiprocketAdapter(client=_StubShiprocketClient([_assign_awb_response()])))
    shipment = await ops.assign_awb(shipment.id, actor=None, courier_id=None)
    assert shipment.awb == "AWB777"
    assert shipment.courier_id is not None

    # TRACKING -> IN_TRANSIT -> DELIVERED
    register_adapter(
        ShiprocketAdapter(client=_StubShiprocketClient([_tracking_response("IN TRANSIT")]))
    )
    shipment = await ops.refresh_tracking_for_shipment(shipment.id, actor=None)
    assert shipment.current_status == ShipmentStatus.IN_TRANSIT

    register_adapter(
        ShiprocketAdapter(client=_StubShiprocketClient([_tracking_response("DELIVERED")]))
    )
    shipment = await ops.refresh_tracking_for_shipment(shipment.id, actor=None)
    assert shipment.current_status == ShipmentStatus.DELIVERED

    # --- Data ownership (spec §10): Shopify-owned order fields must be
    # untouched by every Shiprocket write that happened above.
    reloaded_order = await OrderRepository(db_session).get_by_id(order_id)
    assert reloaded_order is not None
    assert reloaded_order.shopify_order_id == shopify_order_id
    assert reloaded_order.total_amount == total_amount
    assert reloaded_order.source_system == "shopify"

    # Shiprocket-owned shipment fields are populated only via the
    # Shiprocket flow, never invented.
    reloaded_shipment = await ShipmentRepository(db_session).get_by_id(shipment.id)
    assert reloaded_shipment is not None
    assert reloaded_shipment.source_system == "shiprocket"
    assert reloaded_shipment.shiprocket_shipment_id == "5001"

    # --- Audit trail (spec §20): every step in the chain left a record.
    logs, _ = await AuditLogRepository(db_session).list(
        page_params=_page(), sort_params=_sort(), query=None
    )
    actions = {log.action for log in logs}
    assert "sync.completed" in actions
    assert "shipment.created_via_shiprocket" in actions
    assert "shipment.awb_assigned" in actions
    assert "shipment.tracking_refreshed" in actions

    # No credential ever appears in an audit log's stored values.
    for log in logs:
        for value in (log.previous_value, log.new_value, log.audit_metadata):
            if value:
                assert "super-secret-password" not in str(value)


# Data ownership: resyncing the Shopify order after the Shiprocket
# shipment exists must not touch Shiprocket-owned fields, and vice versa.
async def test_resyncing_shopify_order_does_not_disturb_shiprocket_owned_shipment(
    db_session: AsyncSession,
) -> None:
    order = await _sync_one_shopify_order(db_session)

    register_adapter(ShiprocketAdapter(client=_StubShiprocketClient([_create_order_response()])))
    ops = ShiprocketOperationsService(db_session)
    shipment = await ops.create_shipment_for_order(order.id, actor=None)
    register_adapter(ShiprocketAdapter(client=_StubShiprocketClient([_assign_awb_response()])))
    shipment = await ops.assign_awb(shipment.id, actor=None, courier_id=None)

    # Resync the same Shopify order (e.g. a periodic incremental sync).
    integration = (
        await db_session.execute(
            select(Integration).where(Integration.code == IntegrationCode.SHOPIFY)
        )
    ).scalar_one()
    register_adapter(ShopifyAdapter(client=_StubShopifyClient(_shopify_order_page())))
    job2 = await SyncService(db_session).run_sync(
        integration_id=integration.id, sync_type=SyncType.FULL, entity_type="orders"
    )
    assert job2.status.value == "completed"

    orders, total = await OrderRepository(db_session).list(page_params=_page(), sort_params=_sort())
    assert total == 1  # no duplicate order created by the resync

    reloaded_shipment = await ShipmentRepository(db_session).get_by_id(shipment.id)
    assert reloaded_shipment is not None
    assert reloaded_shipment.awb == "AWB777"
    assert reloaded_shipment.shiprocket_shipment_id == "5001"


def _page():
    from app.schemas.common import PageParams

    return PageParams(page=1, page_size=50)


def _sort():
    from app.schemas.common import SortParams

    return SortParams(sort_by="created_at", sort_order="desc")

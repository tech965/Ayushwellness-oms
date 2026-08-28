"""Reconciliation engine — Phase 2.4.

`ReconciliationService.run_checks` is tested directly against
`db_session` (matching `test_shopify_sync.py`/`test_shiprocket_sync.py`'s
convention) rather than through the Celery-dispatching trigger endpoint,
so these tests never depend on a real broker. RBAC/credential-protection
style tests do go through the real HTTP client, mirroring
`test_sync_jobs_and_integrations_endpoints_are_permission_gated`.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from app.integrations.registry import (
    clear_adapters,
    register_adapter,
    restore_adapters,
    snapshot_adapters,
)
from app.integrations.shiprocket.adapter import ShiprocketAdapter
from app.integrations.shopify.adapter import ShopifyAdapter
from app.models.enums import (
    PaymentType,
    ReconciliationRunStatus,
    ReconciliationStatus,
    ShipmentStatus,
)
from app.models.mixins import SourceSystem
from app.repositories.audit_log import AuditLogRepository
from app.services.audit_service import AuditService
from app.services.courier_service import CourierService
from app.services.customer_service import CustomerService
from app.services.order_service import OrderService
from app.services.product_service import ProductService
from app.services.reconciliation_service import ReconciliationService
from app.services.shipment_service import ShipmentService
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio


class _StubShopifyClient:
    """Dispatches by entity type (parsed from the GraphQL query's operation
    name) rather than call order — `run_checks` always runs all three
    Shopify checks (orders/products/customers) together whenever *any*
    Shopify-backed check is under test, so a purely sequential stub would
    desync after the first call. Defaults to an empty page for entity
    types the test doesn't care about.
    """

    def __init__(self, responses: dict[str, dict]) -> None:
        self._responses = responses

    async def execute(self, query: str, variables: dict | None = None) -> dict:
        if "query Customers" in query:
            return {"customers": self._responses.get("customers", _empty_connection())}
        if "query Products" in query:
            return {"products": self._responses.get("products", _empty_connection())}
        return {"orders": self._responses.get("orders", _empty_connection())}


class _StubShiprocketClient:
    def __init__(self, responses: list) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[str, str]] = []

    async def request(
        self, method: str, path: str, *, json: dict | None = None, params: dict | None = None
    ) -> dict:
        self.calls.append((method, path))
        return self._responses.pop(0)

    async def ensure_authenticated(self) -> None:
        pass


@pytest.fixture(autouse=True)
def _reset_adapter_registry():
    # `app.main`'s import graph transitively imports `app.workers.celery_app`
    # (via the Celery task modules the sync/reconciliation endpoints
    # import), which registers real — but unconfigured — adapter instances
    # at import time (intentional for the actual Celery worker process, per
    # that module's docstring). Clear before *and* after each test so "no
    # adapter registered" tests see a genuinely empty registry rather than
    # that import-time side effect.
    snapshot = snapshot_adapters()
    clear_adapters()
    yield
    restore_adapters(snapshot)


def _empty_connection() -> dict:
    return {"pageInfo": {"hasNextPage": False, "endCursor": None}, "edges": []}


async def _make_order(session: AsyncSession, *, order_number: str) -> object:
    return await OrderService(session).create_order(
        actor=None,
        order_number=order_number,
        customer_id=None,
        order_datetime=datetime.now(UTC),
        currency="INR",
        payment_type=PaymentType.PREPAID,
        shipping_charge=0,
        notes=None,
        items=[],
    )


# 21. Reconciliation mismatch / 22. Reconciliation missing record


async def test_no_adapters_registered_skips_every_provider_check(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Round 9: `get_adapter` now self-heals a registry that's merely
    empty (see `app.integrations.registry.get_adapter`'s docstring) —
    `clear_adapters()` alone can no longer represent "neither provider
    is available" for Shopify/Shiprocket specifically, since both have
    a real `register()` and will always be found again on the very next
    lookup. What "no adapters" actually still looks like post-fix is
    "registered, but genuinely unconfigured" (no credentials) — every
    check that touches the adapter still gracefully reports itself
    skipped, exactly as before, just via a different internal path
    (`_safe_check` catching the adapter's own "not configured"
    `IntegrationError`, not the `adapter is None` branch this test used
    to exercise). Config is forced to `None` explicitly here rather than
    relying on the environment having no real credentials, so this test
    can't accidentally start making live API calls in a dev environment
    that *does* have them configured (e.g. a real Shopify token in
    `.env`) — confirmed that was a real risk while fixing this test.
    """
    from app.integrations.shiprocket.config import ShiprocketConfig
    from app.integrations.shopify.config import ShopifyConfig

    monkeypatch.setattr(ShopifyConfig, "from_settings", classmethod(lambda cls: None))
    monkeypatch.setattr(ShiprocketConfig, "from_settings", classmethod(lambda cls: None))

    service = ReconciliationService(db_session)
    run = await service.start_run(actor=None)
    completed = await service.run_checks(run.id)

    assert completed.status == ReconciliationRunStatus.COMPLETED
    assert completed.run_metadata is not None
    skipped = completed.run_metadata["skipped_checks"]
    assert "shopify_order_missing_in_oms" in skipped
    assert "shiprocket_ndr_mismatch" in skipped
    assert completed.run_metadata["errored_checks"] == []


async def test_oms_order_missing_shopify_id_is_reported_as_mismatch(
    db_session: AsyncSession,
) -> None:
    order = await OrderService(db_session).create_order(
        actor=None,
        order_number="OMS-100",
        customer_id=None,
        order_datetime=datetime.now(UTC),
        currency="INR",
        payment_type=PaymentType.PREPAID,
        shipping_charge=0,
        notes=None,
        items=[],
    )
    # Simulate a Shopify-sourced order whose shopify_order_id never landed.
    from app.repositories.order import OrderRepository

    await OrderRepository(db_session).update(order, source_system=SourceSystem.SHOPIFY)
    await db_session.commit()

    service = ReconciliationService(db_session)
    run = await service.start_run(actor=None)
    completed = await service.run_checks(run.id)

    items, total = await service.list_results(
        page_params=_page(), sort_params=_sort(), check_type="oms_order_missing_shopify_id"
    )
    assert total == 1
    assert items[0].status == ReconciliationStatus.MISMATCH
    assert items[0].internal_id == str(order.id)
    assert completed.mismatch_count >= 1


async def test_shipment_missing_shiprocket_id_is_reported_as_mismatch(
    db_session: AsyncSession,
) -> None:
    order = await _make_order(db_session, order_number="OMS-101")
    shipment = await ShipmentService(db_session).create_shipment(
        actor=None, order_id=order.id, awb=None, courier_id=None, expected_delivery_date=None
    )
    await ShipmentService(db_session).update_shipment(
        shipment.id, actor=None, source_system=SourceSystem.SHIPROCKET
    )

    service = ReconciliationService(db_session)
    run = await service.start_run(actor=None)
    await service.run_checks(run.id)

    items, total = await service.list_results(
        page_params=_page(), sort_params=_sort(), check_type="shipment_missing_shiprocket_id"
    )
    assert total == 1
    assert items[0].status == ReconciliationStatus.MISMATCH
    assert items[0].internal_id == str(shipment.id)


async def test_shiprocket_shipment_missing_in_oms_detected_via_audit_trail(
    db_session: AsyncSession,
) -> None:
    order = await _make_order(db_session, order_number="OMS-102")
    shipment = await ShipmentService(db_session).create_shipment(
        actor=None, order_id=order.id, awb=None, courier_id=None, expected_delivery_date=None
    )
    fake_shipment_id = "11111111-1111-1111-1111-111111111111"
    await AuditService(db_session).record(
        user=None,
        action="shipment.created_via_shiprocket",
        entity_type="shipment",
        entity_id=fake_shipment_id,
    )
    await db_session.commit()
    # A real shipment id also gets an audit row — must NOT be reported.
    await AuditService(db_session).record(
        user=None,
        action="shipment.created_via_shiprocket",
        entity_type="shipment",
        entity_id=str(shipment.id),
    )
    await db_session.commit()

    service = ReconciliationService(db_session)
    run = await service.start_run(actor=None)
    await service.run_checks(run.id)

    items, total = await service.list_results(
        page_params=_page(),
        sort_params=_sort(),
        check_type="shiprocket_shipment_missing_in_oms",
    )
    assert total == 1
    assert items[0].status == ReconciliationStatus.MISSING
    assert items[0].internal_id == fake_shipment_id


async def test_shopify_order_missing_in_oms_reports_missing_then_reconciled_after_sync(
    db_session: AsyncSession,
) -> None:
    client = _StubShopifyClient(
        {
            "orders": {
                "pageInfo": {"hasNextPage": False, "endCursor": None},
                "edges": [
                    {
                        "node": {
                            "id": "gid://shopify/Order/900",
                            "name": "#900",
                            "createdAt": "2026-01-01T00:00:00Z",
                            "updatedAt": "2026-01-02T00:00:00Z",
                            "cancelledAt": None,
                            "currencyCode": "INR",
                            "displayFinancialStatus": "PAID",
                            "displayFulfillmentStatus": "UNFULFILLED",
                            "subtotalPriceSet": {"shopMoney": {"amount": "500.00"}},
                            "totalDiscountsSet": {"shopMoney": {"amount": "0.00"}},
                            "totalTaxSet": {"shopMoney": {"amount": "0.00"}},
                            "totalPriceSet": {"shopMoney": {"amount": "500.00"}},
                            "shippingLine": None,
                            "paymentGatewayNames": ["Razorpay"],
                            "customer": None,
                            "shippingAddress": None,
                            "billingAddress": None,
                            "lineItems": {"edges": []},
                        }
                    }
                ],
            }
        }
    )
    register_adapter(ShopifyAdapter(client=client))

    service = ReconciliationService(db_session)
    run = await service.start_run(actor=None)
    await service.run_checks(run.id)

    items, total = await service.list_results(
        page_params=_page(), sort_params=_sort(), check_type="shopify_order_missing_in_oms"
    )
    assert total == 1
    assert items[0].status == ReconciliationStatus.MISSING
    assert items[0].external_id == "900"


async def test_shopify_product_diff_detects_title_mismatch(db_session: AsyncSession) -> None:
    from app.models.enums import ProductStatus

    product, _ = await ProductService(db_session).upsert_synced_product(
        source_system=SourceSystem.SHOPIFY,
        external_id="500",
        shopify_product_id="500",
        title="Old Title",
        status=ProductStatus.ACTIVE,
        vendor="AyushWellness",
        variants=[
            {
                "source_system": SourceSystem.SHOPIFY,
                "external_id": "v500",
                "shopify_variant_id": "v500",
                "sku": "SKU-500",
                "price": "100.00",
                "status": ProductStatus.ACTIVE,
            }
        ],
    )

    client = _StubShopifyClient(
        {
            "products": {
                "pageInfo": {"hasNextPage": False, "endCursor": None},
                "edges": [
                    {
                        "node": {
                            "id": "gid://shopify/Product/500",
                            "title": "New Title",
                            "vendor": "AyushWellness",
                            "productType": "Supplement",
                            "status": "ACTIVE",
                            "tags": [],
                            "createdAt": "2026-01-01T00:00:00Z",
                            "updatedAt": "2026-01-02T00:00:00Z",
                            "variants": {"edges": []},
                        }
                    }
                ],
            }
        }
    )
    register_adapter(ShopifyAdapter(client=client))

    service = ReconciliationService(db_session)
    run = await service.start_run(actor=None)
    await service.run_checks(run.id)

    items, total = await service.list_results(
        page_params=_page(), sort_params=_sort(), check_type="shopify_product_diff"
    )
    assert total == 1
    assert items[0].status == ReconciliationStatus.MISMATCH
    assert items[0].internal_id == str(product.id)
    assert items[0].expected_value["shopify"]["title"] == {
        "oms": "Old Title",
        "shopify": "New Title",
    }


async def test_shopify_customer_diff_reconciled_when_fields_match(
    db_session: AsyncSession,
) -> None:
    customer, _ = await CustomerService(db_session).upsert_synced_customer(
        source_system=SourceSystem.SHOPIFY,
        external_id="700",
        shopify_customer_id="700",
        first_name="Jane",
        last_name="Doe",
        email="jane@example.com",
    )

    client = _StubShopifyClient(
        {
            "customers": {
                "pageInfo": {"hasNextPage": False, "endCursor": None},
                "edges": [
                    {
                        "node": {
                            "id": "gid://shopify/Customer/700",
                            "firstName": "Jane",
                            "lastName": "Doe",
                            "email": "jane@example.com",
                            "phone": None,
                            "state": "ENABLED",
                            "createdAt": "2026-01-01T00:00:00Z",
                            "updatedAt": "2026-01-02T00:00:00Z",
                            "defaultAddress": None,
                            "addresses": [],
                        }
                    }
                ],
            }
        }
    )
    register_adapter(ShopifyAdapter(client=client))

    service = ReconciliationService(db_session)
    run = await service.start_run(actor=None)
    await service.run_checks(run.id)

    items, total = await service.list_results(
        page_params=_page(), sort_params=_sort(), check_type="shopify_customer_diff"
    )
    assert total == 1
    assert items[0].status == ReconciliationStatus.RECONCILED
    assert items[0].internal_id == str(customer.id)


async def test_shiprocket_tracking_family_detects_awb_courier_status_and_missing_rto(
    db_session: AsyncSession,
) -> None:
    order = await _make_order(db_session, order_number="OMS-200")
    courier, _ = await CourierService(db_session).upsert_synced_courier(
        source_system=SourceSystem.SHIPROCKET, external_id="c1", name="Delhivery"
    )
    shipment = await ShipmentService(db_session).create_shipment(
        actor=None,
        order_id=order.id,
        awb="AWB200",
        courier_id=courier.id,
        expected_delivery_date=None,
    )
    await ShipmentService(db_session).update_shipment(
        shipment.id,
        actor=None,
        shiprocket_shipment_id="SR200",
        current_status=ShipmentStatus.IN_TRANSIT,
    )

    tracking_response = {
        "tracking_data": {
            "awb": "AWB999-WRONG",
            "shipment_track_activities": [
                {
                    "id": 1,
                    "status": "RTO INITIATED",
                    "date": "2026-01-05 10:00:00",
                    "activity": "RTO INITIATED",
                    "courier_name": "Bluedart",
                }
            ],
        }
    }
    client = _StubShiprocketClient([tracking_response])
    register_adapter(ShiprocketAdapter(client=client))

    service = ReconciliationService(db_session)
    run = await service.start_run(actor=None)
    await service.run_checks(run.id)

    awb_items, _ = await service.list_results(
        page_params=_page(), sort_params=_sort(), check_type="awb_mismatch"
    )
    assert awb_items[0].status == ReconciliationStatus.MISMATCH

    courier_items, _ = await service.list_results(
        page_params=_page(), sort_params=_sort(), check_type="courier_mismatch"
    )
    assert courier_items[0].status == ReconciliationStatus.MISMATCH
    assert courier_items[0].expected_value == {"courier": "Bluedart"}

    status_items, _ = await service.list_results(
        page_params=_page(), sort_params=_sort(), check_type="tracking_status_mismatch"
    )
    assert status_items[0].status == ReconciliationStatus.MISMATCH
    assert status_items[0].expected_value == {"status": "rto_initiated"}

    rto_items, _ = await service.list_results(
        page_params=_page(), sort_params=_sort(), check_type="rto_mismatch"
    )
    assert rto_items[0].status == ReconciliationStatus.MISSING


async def test_shiprocket_tracking_family_reconciled_when_everything_matches(
    db_session: AsyncSession,
) -> None:
    order = await _make_order(db_session, order_number="OMS-201")
    shipment = await ShipmentService(db_session).create_shipment(
        actor=None, order_id=order.id, awb="AWB201", courier_id=None, expected_delivery_date=None
    )
    await ShipmentService(db_session).update_shipment(
        shipment.id,
        actor=None,
        shiprocket_shipment_id="SR201",
        current_status=ShipmentStatus.IN_TRANSIT,
    )

    tracking_response = {
        "tracking_data": {
            "awb": "AWB201",
            "shipment_track_activities": [
                {
                    "id": 1,
                    "status": "IN TRANSIT",
                    "date": "2026-01-05 10:00:00",
                    "activity": "IN TRANSIT",
                }
            ],
        }
    }
    client = _StubShiprocketClient([tracking_response])
    register_adapter(ShiprocketAdapter(client=client))

    service = ReconciliationService(db_session)
    run = await service.start_run(actor=None)
    completed = await service.run_checks(run.id)

    awb_items, _ = await service.list_results(
        page_params=_page(), sort_params=_sort(), check_type="awb_mismatch"
    )
    assert awb_items[0].status == ReconciliationStatus.RECONCILED
    status_items, _ = await service.list_results(
        page_params=_page(), sort_params=_sort(), check_type="tracking_status_mismatch"
    )
    assert status_items[0].status == ReconciliationStatus.RECONCILED
    assert completed.mismatch_count == 0


async def test_ndr_mismatch_reports_missing_ndr(db_session: AsyncSession) -> None:
    order = await _make_order(db_session, order_number="OMS-300")
    await ShipmentService(db_session).create_shipment(
        actor=None, order_id=order.id, awb="AWB300", courier_id=None, expected_delivery_date=None
    )

    client = _StubShiprocketClient(
        [
            {
                "data": [
                    {
                        "id": 1,
                        "awb": "AWB300",
                        "order_id": "1",
                        "reason": "Customer unavailable",
                        "attempts": 1,
                    }
                ],
                "meta": {"pagination": {"total_pages": 1}},
            }
        ]
    )
    register_adapter(ShiprocketAdapter(client=client))

    service = ReconciliationService(db_session)
    run = await service.start_run(actor=None)
    await service.run_checks(run.id)

    items, total = await service.list_results(
        page_params=_page(), sort_params=_sort(), check_type="ndr_mismatch"
    )
    assert total == 1
    assert items[0].status == ReconciliationStatus.MISSING


async def test_resolve_marks_result_resolved_and_writes_audit_log(
    db_session: AsyncSession,
) -> None:
    order = await _make_order(db_session, order_number="OMS-400")
    shipment = await ShipmentService(db_session).create_shipment(
        actor=None, order_id=order.id, awb=None, courier_id=None, expected_delivery_date=None
    )
    await ShipmentService(db_session).update_shipment(
        shipment.id, actor=None, source_system=SourceSystem.SHIPROCKET
    )

    service = ReconciliationService(db_session)
    run = await service.start_run(actor=None)
    await service.run_checks(run.id)

    items, _ = await service.list_results(
        page_params=_page(), sort_params=_sort(), check_type="shipment_missing_shiprocket_id"
    )
    result = items[0]
    assert result.resolved is False

    from app.core.security import hash_password
    from app.models.auth import User

    actor = User(
        name="Ops",
        email="ops@example.com",
        password_hash=hash_password("Test1234!"),
        is_active=True,
    )
    db_session.add(actor)
    await db_session.flush()
    await db_session.commit()

    resolved = await service.resolve_result(result.id, actor=actor)
    assert resolved.resolved is True
    assert resolved.resolved_by_user_id == actor.id

    logs, total = await AuditLogRepository(db_session).list(
        page_params=_page(), sort_params=_sort()
    )
    assert any(log.action == "reconciliation.result_resolved" for log in logs)


# RBAC


async def test_reconciliation_endpoints_require_authentication(db_session: AsyncSession) -> None:
    from app.db.session import get_db
    from app.main import app
    from httpx import ASGITransport, AsyncClient

    async def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        assert (await client.get("/api/v1/reconciliation/runs")).status_code == 401
        assert (await client.post("/api/v1/reconciliation/runs")).status_code == 401
    app.dependency_overrides.clear()


async def test_reconciliation_endpoints_require_permission(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    async with await make_authenticated_client(db_session, permission_codes=[]) as client:
        assert (await client.get("/api/v1/reconciliation/runs")).status_code == 403
        assert (await client.get("/api/v1/reconciliation/results")).status_code == 403
        assert (await client.post("/api/v1/reconciliation/runs")).status_code == 403


async def test_reconciliation_read_permission_allows_listing_runs(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    service = ReconciliationService(db_session)
    await service.start_run(actor=None)

    async with await make_authenticated_client(
        db_session, permission_codes=["reconciliation.read"]
    ) as client:
        response = await client.get("/api/v1/reconciliation/runs")
        assert response.status_code == 200
        assert response.json()["meta"]["total_items"] == 1
        # read-only permission must not allow triggering a run
        assert (await client.post("/api/v1/reconciliation/runs")).status_code == 403


def _page():
    from app.schemas.common import PageParams

    return PageParams(page=1, page_size=50)


def _sort():
    from app.schemas.common import SortParams

    return SortParams(sort_by="created_at", sort_order="desc")

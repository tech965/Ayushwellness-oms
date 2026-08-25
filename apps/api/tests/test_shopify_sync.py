"""End-to-end Shopify sync through the real `SyncService` pipeline —
`SyncJob` -> `ShopifyAdapter` (stubbed at the HTTP boundary) ->
normalizer -> `CustomerService`/`ProductService`/`OrderService` ->
`CustomerRepository`/... . No real Shopify account; the stub client
returns hand-built GraphQL response shapes.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from app.integrations.registry import clear_adapters, register_adapter
from app.integrations.shopify.adapter import ShopifyAdapter
from app.models.customer import Customer
from app.models.enums import IntegrationStatus, IntegrationType, SyncType
from app.models.integration import Integration, IntegrationCode
from app.models.order import Order
from app.models.product import Product
from app.repositories.customer import CustomerRepository
from app.repositories.integration import IntegrationRepository
from app.repositories.order import OrderRepository
from app.repositories.payment import PaymentRepository
from app.repositories.product import ProductRepository
from app.services.sync_service import SyncService
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio


class _StubClient:
    def __init__(self, responses: list) -> None:
        self._responses = list(responses)
        self.calls: list[dict | None] = []

    async def execute(self, query: str, variables: dict | None = None) -> dict:
        self.calls.append(variables)
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


@pytest.fixture(autouse=True)
def _reset_adapter_registry():
    yield
    clear_adapters()


async def _make_shopify_integration(session: AsyncSession) -> Integration:
    integration = await IntegrationRepository(session).create(
        name="Shopify",
        code=IntegrationCode.SHOPIFY,
        type=IntegrationType.ECOMMERCE,
        status=IntegrationStatus.DISCONNECTED,
        enabled=True,
    )
    await session.commit()
    return integration


def _customers_response(customer_id: str, *, email: str = "a@example.com") -> dict:
    return {
        "customers": {
            "pageInfo": {"hasNextPage": False, "endCursor": None},
            "edges": [
                {
                    "node": {
                        "id": f"gid://shopify/Customer/{customer_id}",
                        "firstName": "Jane",
                        "lastName": "Doe",
                        "email": email,
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


def _products_response(product_id: str, variant_id: str, sku: str) -> dict:
    return {
        "products": {
            "pageInfo": {"hasNextPage": False, "endCursor": None},
            "edges": [
                {
                    "node": {
                        "id": f"gid://shopify/Product/{product_id}",
                        "title": "Ashwagandha",
                        "vendor": "AyushWellness",
                        "productType": "Supplement",
                        "status": "ACTIVE",
                        "tags": ["herbal"],
                        "createdAt": "2026-01-01T00:00:00Z",
                        "updatedAt": "2026-01-02T00:00:00Z",
                        "variants": {
                            "edges": [
                                {
                                    "node": {
                                        "id": f"gid://shopify/ProductVariant/{variant_id}",
                                        "sku": sku,
                                        "title": None,
                                        "price": "499.00",
                                        "compareAtPrice": None,
                                        "inventoryQuantity": 10,
                                        "weight": None,
                                        "barcode": None,
                                        "selectedOptions": [],
                                    }
                                }
                            ]
                        },
                    }
                }
            ],
        }
    }


def _orders_response(order_id: str) -> dict:
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
                        "subtotalPriceSet": {"shopMoney": {"amount": "500.00"}},
                        "totalDiscountsSet": {"shopMoney": {"amount": "0.00"}},
                        "totalTaxSet": {"shopMoney": {"amount": "0.00"}},
                        "totalPriceSet": {"shopMoney": {"amount": "500.00"}},
                        "shippingLine": None,
                        "paymentGatewayNames": ["Razorpay"],
                        "customer": None,
                        "shippingAddress": None,
                        "billingAddress": None,
                        "lineItems": {
                            "edges": [
                                {
                                    "node": {
                                        "id": "gid://shopify/LineItem/1",
                                        "sku": "ASH-60",
                                        "title": "Ashwagandha",
                                        "quantity": 1,
                                        "originalUnitPriceSet": {"shopMoney": {"amount": "500.00"}},
                                        "discountedTotalSet": {"shopMoney": {"amount": "500.00"}},
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


# 5. Customer upsert / 20. SyncJob lifecycle
async def test_customer_sync_creates_customer_and_completes_job(db_session: AsyncSession) -> None:
    client = _StubClient([_customers_response("1")])
    register_adapter(ShopifyAdapter(client=client))
    integration = await _make_shopify_integration(db_session)

    service = SyncService(db_session)
    job = await service.start_sync(
        integration_id=integration.id, sync_type=SyncType.FULL, entity_type="customers"
    )
    assert job.status == "queued"

    job = await service.execute_sync(job.id)

    assert job.status == "completed"
    assert job.records_received == 1
    assert job.records_created == 1
    assert job.completed_at is not None

    customer = await CustomerRepository(db_session).get_by_source_external_id(
        source_system="shopify", external_id="1"
    )
    assert customer is not None
    assert customer.email == "a@example.com"

    refreshed_integration = await IntegrationRepository(db_session).get_by_id(integration.id)
    assert refreshed_integration.status == "connected"
    assert refreshed_integration.last_successful_sync_at is not None


# 6. Duplicate customer prevention
async def test_resyncing_the_same_customer_updates_instead_of_duplicating(
    db_session: AsyncSession,
) -> None:
    client = _StubClient(
        [
            _customers_response("2", email="first@example.com"),
            _customers_response("2", email="second@example.com"),
        ]
    )
    register_adapter(ShopifyAdapter(client=client))
    integration = await _make_shopify_integration(db_session)
    service = SyncService(db_session)

    job1 = await service.run_sync(
        integration_id=integration.id, sync_type=SyncType.FULL, entity_type="customers"
    )
    job2 = await service.run_sync(
        integration_id=integration.id, sync_type=SyncType.FULL, entity_type="customers"
    )

    assert job1.records_created == 1
    assert job2.records_created == 0
    assert job2.records_updated == 1

    total = await db_session.execute(select(func.count()).select_from(Customer))
    assert total.scalar_one() == 1

    customer = await CustomerRepository(db_session).get_by_source_external_id(
        source_system="shopify", external_id="2"
    )
    assert customer.email == "second@example.com"


# 9. Product upsert
async def test_product_sync_creates_product_with_variant(db_session: AsyncSession) -> None:
    client = _StubClient([_products_response("10", "20", "ASH-60")])
    register_adapter(ShopifyAdapter(client=client))
    integration = await _make_shopify_integration(db_session)

    service = SyncService(db_session)
    job = await service.run_sync(
        integration_id=integration.id, sync_type=SyncType.FULL, entity_type="products"
    )

    assert job.status == "completed"
    repo = ProductRepository(db_session)
    created = await repo.get_by_source_external_id(source_system="shopify", external_id="10")
    assert created is not None
    product = await repo.get_by_id_with_variants(created.id)
    assert product is not None
    assert len(product.variants) == 1
    assert product.variants[0].sku == "ASH-60"


# 10. Duplicate product prevention
async def test_resyncing_the_same_product_does_not_duplicate(db_session: AsyncSession) -> None:
    client = _StubClient(
        [_products_response("11", "21", "ASH-90"), _products_response("11", "21", "ASH-90")]
    )
    register_adapter(ShopifyAdapter(client=client))
    integration = await _make_shopify_integration(db_session)
    service = SyncService(db_session)

    await service.run_sync(
        integration_id=integration.id, sync_type=SyncType.FULL, entity_type="products"
    )
    await service.run_sync(
        integration_id=integration.id, sync_type=SyncType.FULL, entity_type="products"
    )

    total = await db_session.execute(select(func.count()).select_from(Product))
    assert total.scalar_one() == 1


# 13. Order upsert / payment creation
async def test_order_sync_creates_order_with_items_and_payment(db_session: AsyncSession) -> None:
    client = _StubClient([_orders_response("500")])
    register_adapter(ShopifyAdapter(client=client))
    integration = await _make_shopify_integration(db_session)

    service = SyncService(db_session)
    job = await service.run_sync(
        integration_id=integration.id, sync_type=SyncType.FULL, entity_type="orders"
    )

    assert job.status == "completed"
    order = await OrderRepository(db_session).get_by_source_external_id(
        source_system="shopify", external_id="500"
    )
    assert order is not None
    assert order.order_number == "#500"
    assert order.payment_status == "paid"
    assert order.status == "confirmed"  # PAID at creation -> initial OMS status CONFIRMED

    payments = await PaymentRepository(db_session).list_for_order(order.id)
    assert len(payments) == 1
    assert payments[0].status == "paid"


# 14. Duplicate order prevention
async def test_resyncing_the_same_order_does_not_duplicate(db_session: AsyncSession) -> None:
    client = _StubClient([_orders_response("501"), _orders_response("501")])
    register_adapter(ShopifyAdapter(client=client))
    integration = await _make_shopify_integration(db_session)
    service = SyncService(db_session)

    await service.run_sync(
        integration_id=integration.id, sync_type=SyncType.FULL, entity_type="orders"
    )
    await service.run_sync(
        integration_id=integration.id, sync_type=SyncType.FULL, entity_type="orders"
    )

    total = await db_session.execute(select(func.count()).select_from(Order))
    assert total.scalar_one() == 1


# 19. Partial sync failure
async def test_one_bad_record_does_not_fail_the_whole_sync_job(db_session: AsyncSession) -> None:
    """Two products in one page; the second reuses the first's SKU (a real
    unique-constraint violation), so it fails while the first succeeds —
    the job must land PARTIAL, not FAILED, per spec §23.
    """
    page = _products_response("30", "40", "DUP-SKU")
    page["products"]["edges"].append(
        {
            "node": {
                "id": "gid://shopify/Product/31",
                "title": "Conflicting Product",
                "vendor": None,
                "productType": None,
                "status": "ACTIVE",
                "tags": None,
                "createdAt": "2026-01-01T00:00:00Z",
                "updatedAt": "2026-01-02T00:00:00Z",
                "variants": {
                    "edges": [
                        {
                            "node": {
                                "id": "gid://shopify/ProductVariant/41",
                                "sku": "DUP-SKU",
                                "title": None,
                                "price": "1.00",
                                "compareAtPrice": None,
                                "inventoryQuantity": 0,
                                "weight": None,
                                "barcode": None,
                                "selectedOptions": [],
                            }
                        }
                    ]
                },
            }
        }
    )
    client = _StubClient([page])
    register_adapter(ShopifyAdapter(client=client))
    integration = await _make_shopify_integration(db_session)

    service = SyncService(db_session)
    job = await service.run_sync(
        integration_id=integration.id, sync_type=SyncType.FULL, entity_type="products"
    )

    assert job.status == "partial"
    assert job.records_received == 2
    assert job.records_created == 1
    assert job.records_failed == 1
    assert job.error_count == 1

    total = await db_session.execute(select(func.count()).select_from(Product))
    assert total.scalar_one() == 1


# 25. Incremental sync
async def test_incremental_sync_passes_last_successful_sync_at_as_filter(
    db_session: AsyncSession,
) -> None:
    client = _StubClient([_customers_response("60")])
    register_adapter(ShopifyAdapter(client=client))
    integration = await _make_shopify_integration(db_session)
    await IntegrationRepository(db_session).update(
        integration, last_successful_sync_at=datetime(2026, 1, 1, tzinfo=UTC)
    )
    await db_session.commit()

    service = SyncService(db_session)
    job = await service.start_sync(
        integration_id=integration.id, sync_type=SyncType.INCREMENTAL, entity_type="customers"
    )
    await service.execute_sync(job.id)

    assert client.calls[0]["query"] is not None
    assert "updated_at" in client.calls[0]["query"]


async def test_full_sync_does_not_apply_an_incremental_filter(db_session: AsyncSession) -> None:
    client = _StubClient([_customers_response("61")])
    register_adapter(ShopifyAdapter(client=client))
    integration = await _make_shopify_integration(db_session)

    service = SyncService(db_session)
    job = await service.start_sync(
        integration_id=integration.id, sync_type=SyncType.FULL, entity_type="customers"
    )
    await service.execute_sync(job.id)

    assert client.calls[0]["query"] is None


# 26. RBAC
async def test_sync_jobs_and_integrations_endpoints_are_permission_gated(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    integration = await _make_shopify_integration(db_session)
    async with await make_authenticated_client(db_session, permission_codes=[]) as client:
        assert (await client.get("/api/v1/integrations")).status_code == 403
        assert (await client.get(f"/api/v1/integrations/{integration.id}")).status_code == 403
        assert (
            await client.post(
                f"/api/v1/sync/{integration.id}/trigger", json={"entity_type": "orders"}
            )
        ).status_code == 403


# 27. Credential protection
async def test_shopify_access_token_never_appears_in_api_response(
    db_session: AsyncSession, make_authenticated_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.core.config import settings

    monkeypatch.setattr(settings, "SHOPIFY_ACCESS_TOKEN", "shpat_super_secret_value")
    monkeypatch.setattr(settings, "SHOPIFY_STORE_DOMAIN", "test-shop.myshopify.com")

    integration = await _make_shopify_integration(db_session)
    async with await make_authenticated_client(
        db_session, permission_codes=["integrations.read"]
    ) as client:
        response = await client.get(f"/api/v1/integrations/{integration.id}")
        body_text = response.text
        assert "shpat_super_secret_value" not in body_text

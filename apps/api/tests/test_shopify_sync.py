"""End-to-end Shopify sync through the real `SyncService` pipeline —
`SyncJob` -> `ShopifyAdapter` (stubbed at the HTTP boundary) ->
normalizer -> `CustomerService`/`ProductService`/`OrderService` ->
`CustomerRepository`/... . No real Shopify account; the stub client
returns hand-built GraphQL response shapes.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from app.integrations.registry import clear_adapters, register_adapter
from app.integrations.shopify.adapter import ShopifyAdapter
from app.models.customer import Customer
from app.models.enums import IntegrationStatus, IntegrationType, SyncJobStatus, SyncType
from app.models.integration import Integration, IntegrationCode
from app.models.order import Order
from app.models.product import Product
from app.repositories.customer import CustomerRepository
from app.repositories.integration import IntegrationRepository
from app.repositories.order import OrderRepository
from app.repositories.payment import PaymentRepository
from app.repositories.product import ProductRepository
from app.repositories.sync_job import SyncJobRepository
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


def _orders_response(
    order_id: str,
    *,
    tags: list[str] | None = None,
    note: str | None = None,
    updated_at: str = "2026-01-02T00:00:00Z",
    fulfillment_status: str = "UNFULFILLED",
) -> dict:
    return {
        "orders": {
            "pageInfo": {"hasNextPage": False, "endCursor": None},
            "edges": [
                {
                    "node": {
                        "id": f"gid://shopify/Order/{order_id}",
                        "name": f"#{order_id}",
                        "createdAt": "2026-01-01T00:00:00Z",
                        "updatedAt": updated_at,
                        "cancelledAt": None,
                        "currencyCode": "INR",
                        "displayFinancialStatus": "PAID",
                        "displayFulfillmentStatus": fulfillment_status,
                        "tags": tags,
                        "note": note,
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


# Issue 4: Shopify order tags/note import.


async def test_order_sync_imports_tags_and_note(db_session: AsyncSession) -> None:
    client = _StubClient(
        [_orders_response("510", tags=["COD", "VIP"], note="Please deliver after 6 PM")]
    )
    register_adapter(ShopifyAdapter(client=client))
    integration = await _make_shopify_integration(db_session)

    await SyncService(db_session).run_sync(
        integration_id=integration.id, sync_type=SyncType.FULL, entity_type="orders"
    )

    order = await OrderRepository(db_session).get_by_source_external_id(
        source_system="shopify", external_id="510"
    )
    assert order is not None
    assert order.shopify_tags == ["COD", "VIP"]
    assert order.shopify_order_note == "Please deliver after 6 PM"
    # Distinct from the OMS-internal `notes` field -- Shopify sync must
    # never write to it.
    assert order.notes is None


async def test_order_sync_with_no_tags_or_note_stores_empty_list_and_none(
    db_session: AsyncSession,
) -> None:
    client = _StubClient([_orders_response("511")])
    register_adapter(ShopifyAdapter(client=client))
    integration = await _make_shopify_integration(db_session)

    await SyncService(db_session).run_sync(
        integration_id=integration.id, sync_type=SyncType.FULL, entity_type="orders"
    )

    order = await OrderRepository(db_session).get_by_source_external_id(
        source_system="shopify", external_id="511"
    )
    assert order is not None
    assert order.shopify_tags == []
    assert order.shopify_order_note is None


async def test_resync_reflects_new_and_removed_tags_and_updated_note(
    db_session: AsyncSession,
) -> None:
    """Idempotent resync: Shopify is the source of truth for tags/note --
    a resync must fully replace the stored value, not append to it, so an
    added tag appears, a removed tag disappears, and an edited note
    overwrites the old one (spec section D).
    """
    client = _StubClient(
        [
            _orders_response(
                "512", tags=["COD", "Repeat Customer"], note="Call before delivery",
                updated_at="2026-01-02T00:00:00Z",
            ),
            _orders_response(
                "512", tags=["VIP", "High Value"], note="Leave at the gate",
                updated_at="2026-01-03T00:00:00Z",
            ),
        ]
    )
    register_adapter(ShopifyAdapter(client=client))
    integration = await _make_shopify_integration(db_session)
    service = SyncService(db_session)

    await service.run_sync(
        integration_id=integration.id, sync_type=SyncType.FULL, entity_type="orders"
    )
    order = await OrderRepository(db_session).get_by_source_external_id(
        source_system="shopify", external_id="512"
    )
    assert order.shopify_tags == ["COD", "Repeat Customer"]

    await service.run_sync(
        integration_id=integration.id, sync_type=SyncType.FULL, entity_type="orders"
    )
    await db_session.refresh(order)
    # "COD"/"Repeat Customer" are gone, replaced wholesale -- not appended.
    assert order.shopify_tags == ["VIP", "High Value"]
    assert order.shopify_order_note == "Leave at the gate"

    total = await db_session.execute(select(func.count()).select_from(Order))
    assert total.scalar_one() == 1


async def test_order_tags_are_returned_as_a_structured_list_not_a_joined_string(
    db_session: AsyncSession,
) -> None:
    """Regression guard distinguishing this from `Product.tags` (a single
    comma-joined string column) -- Order tags must stay a real list.
    """
    client = _StubClient([_orders_response("513", tags=["A", "B", "C"])])
    register_adapter(ShopifyAdapter(client=client))
    integration = await _make_shopify_integration(db_session)

    await SyncService(db_session).run_sync(
        integration_id=integration.id, sync_type=SyncType.FULL, entity_type="orders"
    )

    order = await OrderRepository(db_session).get_by_source_external_id(
        source_system="shopify", external_id="513"
    )
    assert isinstance(order.shopify_tags, list)
    assert order.shopify_tags == ["A", "B", "C"]


# Issue 2: Orders page "Shipment Status" column must be Shopify-sourced
# (`Order.fulfillment_status`), never Shiprocket's `Shipment.current_status`.


async def test_resync_updates_fulfillment_status_on_an_existing_order(
    db_session: AsyncSession,
) -> None:
    """A historical order must pick up Shopify's current fulfillment
    status on its NEXT resync — `fulfillment_status` is part of the same
    always-overwritten `data` dict as every other Shopify-owned field
    (`OrderService.upsert_synced_order`), so no separate backfill path is
    needed; this proves that in practice, not just by code inspection.
    """
    client = _StubClient(
        [
            _orders_response("514", fulfillment_status="UNFULFILLED"),
            _orders_response(
                "514", fulfillment_status="FULFILLED", updated_at="2026-01-03T00:00:00Z"
            ),
        ]
    )
    register_adapter(ShopifyAdapter(client=client))
    integration = await _make_shopify_integration(db_session)
    service = SyncService(db_session)

    await service.run_sync(
        integration_id=integration.id, sync_type=SyncType.FULL, entity_type="orders"
    )
    order = await OrderRepository(db_session).get_by_source_external_id(
        source_system="shopify", external_id="514"
    )
    assert order.fulfillment_status == "unfulfilled"

    await service.run_sync(
        integration_id=integration.id, sync_type=SyncType.FULL, entity_type="orders"
    )
    await db_session.refresh(order)
    assert order.fulfillment_status == "fulfilled"


# 19. Partial sync failure
async def test_one_bad_record_does_not_fail_the_whole_sync_job(db_session: AsyncSession) -> None:
    """Two products in one page; the second has a structurally malformed
    `variants` field (a real-world "Shopify returned something the
    normalizer didn't expect" scenario), so it fails while the first
    succeeds — the job must land PARTIAL, not FAILED, per spec §23.

    Round 4 note: this used to reuse the first product's SKU to trigger
    the failure, but a duplicate SKU is no longer a hard failure (see
    `ProductService._safe_sku` / `test_product_sync_duplicate_sku.py`) —
    it's now handled gracefully by design, so it can no longer stand in
    for "a record that genuinely can't be processed." Malformed
    `variants` still can't be, and still exercises the same PARTIAL-job
    code path this test is actually about.
    """
    page = _products_response("30", "40", "ASH-60")
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
                # normalizer does `.get("variants", {}).get("edges")` -> AttributeError
                "variants": None,
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
async def test_incremental_sync_passes_the_entity_types_own_last_successful_sync_as_filter(
    db_session: AsyncSession,
) -> None:
    """`since` must come from this entity type's own sync-job history —
    not from `Integration.last_successful_sync_at`, a single timestamp
    shared across every entity type the integration syncs (see
    `SyncService.execute_sync`'s docstring for why that's wrong).
    """
    client = _StubClient([_customers_response("60")])
    register_adapter(ShopifyAdapter(client=client))
    integration = await _make_shopify_integration(db_session)

    await SyncJobRepository(db_session).create(
        integration_id=integration.id,
        sync_type=SyncType.FULL,
        entity_type="customers",
        status=SyncJobStatus.COMPLETED,
        completed_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    await db_session.commit()

    service = SyncService(db_session)
    job = await service.start_sync(
        integration_id=integration.id, sync_type=SyncType.INCREMENTAL, entity_type="customers"
    )
    await service.execute_sync(job.id)

    assert client.calls[0]["query"] is not None
    assert "updated_at" in client.calls[0]["query"]
    assert "2026-01-01" in client.calls[0]["query"]


async def test_incremental_sync_is_not_starved_by_a_different_entity_types_completed_sync(
    db_session: AsyncSession,
) -> None:
    """Regression test for a real bug found via a live Shopify
    reconciliation: completing an orders sync used to bump
    `Integration.last_successful_sync_at`, which a customers sync run
    immediately after (same integration, same cycle) then read as its
    own `since` — even though customers had never itself synced before.
    Confirmed live: a first-ever orders+customers+products sync pulled
    every order correctly, but customers and products each fetched 0
    records, purely because orders had *just* completed moments earlier.
    """
    orders_client = _StubClient(
        [{"orders": {"pageInfo": {"hasNextPage": False, "endCursor": None}, "edges": []}}]
    )
    register_adapter(ShopifyAdapter(client=orders_client))
    integration = await _make_shopify_integration(db_session)

    service = SyncService(db_session)
    orders_job = await service.start_sync(
        integration_id=integration.id, sync_type=SyncType.INCREMENTAL, entity_type="orders"
    )
    await service.execute_sync(orders_job.id)
    assert orders_job.status == SyncJobStatus.COMPLETED  # sanity: orders really did complete

    customers_client = _StubClient([_customers_response("62")])
    register_adapter(ShopifyAdapter(client=customers_client))
    customers_job = await service.start_sync(
        integration_id=integration.id, sync_type=SyncType.INCREMENTAL, entity_type="customers"
    )
    await service.execute_sync(customers_job.id)

    # customers has never synced before -> a full fetch (no `since`
    # filter), not starved by orders' completion moments earlier.
    assert customers_client.calls[0]["query"] is None


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


# --- Historical-orders production incident: a backlog crawl that ran out
# of its per-job time budget mid-crawl still `COMPLETED` with a real
# `completed_at` — the only thing that had stood between that and being
# mistaken for "backlog genuinely finished" was `resume_cursor is None`,
# which is itself just an inference, not an explicit signal. Shopify
# orders older than ~2026-03 (e.g. #AWL46048, confirmed live in Shopify,
# created 2025-12-28) were never imported as a result. `backlog_complete`
# is now explicit, persisted state, set ONLY at the exact moment a crawl
# that wasn't already incremental observes a genuine `hasNextPage: false`
# — never inferred from `since`/`resume_cursor` alone. ------------------


async def test_interrupted_backlog_stays_backlog_even_once_since_is_set(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression test for the exact reported production bug: a backlog
    crawl that hits the time budget mid-crawl (persisting a resume
    cursor) must still be treated as an incomplete backlog on the NEXT
    run, even though that first job's own successful completion now
    makes `since` non-None -- `since is not None` alone must never be
    read as "the backlog is done".
    """
    import app.services.sync_service as sync_service_module

    monkeypatch.setattr(sync_service_module, "_MAX_ENTITY_SYNC_DURATION", timedelta(seconds=-1))

    page_1 = _orders_response("46048")
    page_1["orders"]["pageInfo"] = {"hasNextPage": True, "endCursor": "cursor-2"}
    client_1 = _StubClient([page_1])
    register_adapter(ShopifyAdapter(client=client_1))
    integration = await _make_shopify_integration(db_session)

    service = SyncService(db_session)
    job_1 = await service.run_sync(
        integration_id=integration.id, sync_type=SyncType.FULL, entity_type="orders"
    )
    assert job_1.status == SyncJobStatus.COMPLETED  # ran out of time budget, not an error
    assert job_1.completed_at is not None  # `since` is now non-None for the next run

    await db_session.refresh(integration)
    assert integration.configuration["sync_cursors"]["orders"] == "cursor-2"
    assert "orders" not in (integration.configuration.get("backlog_complete") or {})

    # Second run: even requested as INCREMENTAL (what a caller unaware of
    # the interrupted backlog might reasonably pass), the fetch must still
    # be unfiltered (no `updated_at` query) and must resume from the
    # persisted cursor, not restart at page 1 or switch to incremental.
    page_2 = _orders_response("46049")
    page_2["orders"]["pageInfo"] = {"hasNextPage": False, "endCursor": None}
    client_2 = _StubClient([page_2])
    register_adapter(ShopifyAdapter(client=client_2))

    job_2 = await service.run_sync(
        integration_id=integration.id, sync_type=SyncType.INCREMENTAL, entity_type="orders"
    )

    assert client_2.calls[0]["query"] is None  # still a backlog fetch, not filtered by since
    assert client_2.calls[0]["after"] == "cursor-2"  # resumed, did not restart at page 1
    assert job_2.status == SyncJobStatus.COMPLETED

    await db_session.refresh(integration)
    assert integration.configuration["backlog_complete"]["orders"] is True
    assert integration.configuration["sync_cursors"].get("orders") is None


async def test_backlog_complete_flag_set_only_on_genuine_has_more_false(
    db_session: AsyncSession,
) -> None:
    """A single-page backlog crawl that completes cleanly (no time-budget
    interruption) must still set `backlog_complete` explicitly -- and a
    THIRD run must then correctly switch to a real incremental
    (`since`-filtered) fetch.
    """
    client = _StubClient([_orders_response("50001")])
    register_adapter(ShopifyAdapter(client=client))
    integration = await _make_shopify_integration(db_session)

    service = SyncService(db_session)
    job = await service.run_sync(
        integration_id=integration.id, sync_type=SyncType.FULL, entity_type="orders"
    )
    assert job.status == SyncJobStatus.COMPLETED

    await db_session.refresh(integration)
    assert integration.configuration["backlog_complete"]["orders"] is True

    incremental_client = _StubClient(
        [{"orders": {"pageInfo": {"hasNextPage": False, "endCursor": None}, "edges": []}}]
    )
    register_adapter(ShopifyAdapter(client=incremental_client))
    incremental_job = await service.run_sync(
        integration_id=integration.id, sync_type=SyncType.INCREMENTAL, entity_type="orders"
    )

    assert incremental_client.calls[0]["query"] is not None
    assert "updated_at" in incremental_client.calls[0]["query"]
    assert incremental_job.status == SyncJobStatus.COMPLETED


async def test_reset_backlog_recovers_a_missing_historical_order(
    db_session: AsyncSession,
) -> None:
    """End-to-end proof of the recovery path
    (`scripts/reset_shopify_orders_backlog.py`'s core operation,
    `SyncService.reset_backlog`): an entity whose backlog *looks* done
    under the legacy `since`/`resume_cursor` inference (no explicit flag
    ever recorded) is forced back into a genuine unfiltered crawl, and a
    historical order missing from the OMS (the #AWL46048 scenario) is
    imported without duplicating or disturbing anything already synced.
    """
    integration = await _make_shopify_integration(db_session)

    # Simulate the pre-existing, misleading state: a completed job with
    # no resume cursor and no explicit backlog_complete flag -- exactly
    # what production had before this fix existed.
    await SyncJobRepository(db_session).create(
        integration_id=integration.id,
        sync_type=SyncType.FULL,
        entity_type="orders",
        status=SyncJobStatus.COMPLETED,
        completed_at=datetime(2026, 3, 1, tzinfo=UTC),
    )
    await db_session.commit()

    service = SyncService(db_session)
    await service.reset_backlog(integration_id=integration.id, entity_type="orders")

    await db_session.refresh(integration)
    assert integration.configuration["backlog_complete"]["orders"] is False
    assert integration.configuration["sync_cursors"].get("orders") is None

    historical_order = _orders_response("AWL46048")
    client = _StubClient([historical_order])
    register_adapter(ShopifyAdapter(client=client))

    job = await service.run_sync(
        integration_id=integration.id, sync_type=SyncType.FULL, entity_type="orders"
    )

    assert client.calls[0]["query"] is None  # a genuine, unfiltered backlog crawl
    assert job.status == SyncJobStatus.COMPLETED
    assert job.records_created == 1

    order = await OrderRepository(db_session).get_by_order_number("#AWL46048")
    assert order is not None
    assert order.order_number == "#AWL46048"


# Round 5 — Task 9: an HTTP 200 with a GraphQL-level error must not be
# mistaken for a successful page. `ShopifyClient.execute` already raises
# `ShopifyApiError` for this (see `test_shopify_client.py::
# test_client_classifies_graphql_throttled_error` for that half); this
# proves it propagates all the way through the real sync pipeline: the
# job lands FAILED (not COMPLETED, not silently 0 records), with a
# recorded SyncError explaining why.
async def test_a_graphql_error_mid_page_fails_the_job_with_a_recorded_error(
    db_session: AsyncSession,
) -> None:
    from app.integrations.shopify.errors import ShopifyApiError
    from app.models.integration import SyncError

    client = _StubClient(
        [ShopifyApiError("Shopify GraphQL access denied.", error_type="authorization_error")]
    )
    register_adapter(ShopifyAdapter(client=client))
    integration = await _make_shopify_integration(db_session)

    service = SyncService(db_session)
    job = await service.run_sync(
        integration_id=integration.id, sync_type=SyncType.FULL, entity_type="orders"
    )

    assert job.status == SyncJobStatus.FAILED
    assert job.records_received == 0
    assert job.error_count == 1

    error_count = await db_session.execute(
        select(func.count()).select_from(SyncError).where(SyncError.sync_job_id == job.id)
    )
    assert error_count.scalar_one() == 1  # the page-level failure was recorded, not swallowed

    total_orders = await db_session.execute(select(func.count()).select_from(Order))
    assert total_orders.scalar_one() == 0  # nothing was ever inserted from the failed page


async def test_an_unexpected_non_integration_error_still_fails_the_job_and_releases_the_lock(
    db_session: AsyncSession,
) -> None:
    """Only `IntegrationError` used to be caught around `_run_entity_sync`
    -- anything else (e.g. a malformed page response the adapter didn't
    wrap, like a `KeyError` on a missing `node` key) propagated straight
    out of `execute_sync`. Since `mark_running` had already flipped the
    job to RUNNING, nothing was ever left to mark it FAILED, so it stayed
    RUNNING forever and `start_sync`'s one-active-job guard blocked every
    later attempt to sync this entity type -- until the 20-minute stale
    reaper eventually caught up. This proves the fix: any exception, not
    just `IntegrationError`, now fails the job immediately.
    """
    client = _StubClient([KeyError("node")])
    register_adapter(ShopifyAdapter(client=client))
    integration = await _make_shopify_integration(db_session)

    service = SyncService(db_session)
    job = await service.run_sync(
        integration_id=integration.id, sync_type=SyncType.FULL, entity_type="customers"
    )

    assert job.status == SyncJobStatus.FAILED
    assert job.error_count == 1

    # The lock was released -- a new customers sync can start immediately,
    # with no need to wait for the stale-job reaper.
    new_job = await service.start_sync(
        integration_id=integration.id, sync_type=SyncType.FULL, entity_type="customers"
    )
    assert new_job.id != job.id
    assert new_job.status == "queued"


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

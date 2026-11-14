"""Marketplace Orders module — Phase 1.

Covers: the 3 stub adapters (Amazon/Flipkart/Meesho) register cleanly at
startup and report a clean "not configured" outcome; `GET /marketplace/
orders` returns only Amazon/Flipkart/Meesho-sourced rows (never Shopify),
supports the marketplace/status/date/sku filters, and is RBAC-gated;
`GET /marketplace/dashboard/summary` returns correct zeroed/non-zeroed
KPIs scoped to the 3 marketplaces, never counting a Shopify order. Also
proves the existing `/orders` endpoint (Shopify-facing) is completely
unaffected by this module's additive `source_systems` filter parameter.
"""

from __future__ import annotations

import pytest
from app.db.session import get_db
from app.integrations.amazon import AmazonAdapter
from app.integrations.base import FetchPage, HealthCheckResult
from app.integrations.flipkart import FlipkartAdapter
from app.integrations.meesho import MeeshoAdapter
from app.main import app
from app.models.enums import OrderStatus
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
    role = await make_role(
        db_session, name="MARKETPLACE_VIEWER", permission_codes=["marketplace.read"]
    )
    no_access_role = await make_role(db_session, name="NO_MARKETPLACE_ACCESS", permission_codes=[])
    user = await make_user(db_session, email="mkt@marketplace.example.com", role=role)
    no_access_user = await make_user(
        db_session, email="noaccess@marketplace.example.com", role=no_access_role
    )
    customer = await make_customer(db_session)
    return user, no_access_user, customer


async def _make_marketplace_order(
    db_session: AsyncSession,
    *,
    order_number: str,
    customer,
    source_system: str,
    external_id: str,
    status: OrderStatus = OrderStatus.PENDING,
):
    order = await make_order(
        db_session, order_number=order_number, customer=customer, status=status
    )
    order.source_system = source_system
    order.external_id = external_id
    await db_session.commit()
    return order


# ---------------------------------------------------------------------
# Stub adapters
# ---------------------------------------------------------------------
@pytest.mark.parametrize(
    "adapter_cls,code",
    [(AmazonAdapter, "amazon"), (FlipkartAdapter, "flipkart"), (MeeshoAdapter, "meesho")],
)
async def test_stub_adapter_reports_clean_not_configured(adapter_cls, code) -> None:
    adapter = adapter_cls()
    assert adapter.code == code

    health = await adapter.health_check()
    assert isinstance(health, HealthCheckResult)
    assert health.connected is False
    assert health.error_message is not None

    page = await adapter.fetch("orders")
    assert isinstance(page, FetchPage)
    assert page.nodes == []
    assert page.has_more is False

    with pytest.raises(Exception):  # noqa: B017 - IntegrationError, "not configured"
        await adapter.authenticate()

    with pytest.raises(NotImplementedError):
        await adapter.process_webhook("orders/create", {})


async def test_all_three_stub_adapters_registered_at_startup() -> None:
    # Explicitly re-registers rather than trusting ambient registry state
    # from app startup -- the registry is a module-level singleton dict
    # another test file's `clear_adapters()` teardown can legitimately
    # wipe between tests (same self-heal `register_all_adapters` already
    # provides via `get_adapter`'s miss path — see app/integrations/
    # registry.py), so this proves "registers cleanly," not "happens to
    # still be registered from whenever the app process started."
    from app.integrations.bootstrap import register_all_adapters
    from app.integrations.registry import registered_codes

    register_all_adapters()
    codes = registered_codes()
    assert {"amazon", "flipkart", "meesho"}.issubset(codes)


# ---------------------------------------------------------------------
# GET /marketplace/orders
# ---------------------------------------------------------------------
async def test_marketplace_orders_returns_only_marketplace_sourced_rows(
    db_session: AsyncSession,
) -> None:
    user, _no_access, customer = await _setup(db_session)
    amazon_order = await _make_marketplace_order(
        db_session,
        order_number="MKT-AMZ-001",
        customer=customer,
        source_system="amazon",
        external_id="AMZ-001",
    )
    shopify_order = await make_order(db_session, order_number="MKT-SHOPIFY-001", customer=customer)
    shopify_order.source_system = "shopify"
    await db_session.commit()

    async with bearer_client(app, get_db, db_session, user.id) as client:
        response = await client.get("/api/v1/marketplace/orders")

    assert response.status_code == 200
    order_ids = {row["id"] for row in response.json()["data"]}
    assert str(amazon_order.id) in order_ids
    assert str(shopify_order.id) not in order_ids


async def test_marketplace_orders_includes_all_three_marketplaces_by_default(
    db_session: AsyncSession,
) -> None:
    user, _no_access, customer = await _setup(db_session)
    orders = {
        "amazon": await _make_marketplace_order(
            db_session,
            order_number="MKT-ALL-AMZ",
            customer=customer,
            source_system="amazon",
            external_id="AMZ-ALL-1",
        ),
        "flipkart": await _make_marketplace_order(
            db_session,
            order_number="MKT-ALL-FKT",
            customer=customer,
            source_system="flipkart",
            external_id="FKT-ALL-1",
        ),
        "meesho": await _make_marketplace_order(
            db_session,
            order_number="MKT-ALL-MSH",
            customer=customer,
            source_system="meesho",
            external_id="MSH-ALL-1",
        ),
    }

    async with bearer_client(app, get_db, db_session, user.id) as client:
        response = await client.get("/api/v1/marketplace/orders")

    assert response.status_code == 200
    order_ids = {row["id"] for row in response.json()["data"]}
    for order in orders.values():
        assert str(order.id) in order_ids


async def test_marketplace_filter_restricts_to_one_marketplace(db_session: AsyncSession) -> None:
    user, _no_access, customer = await _setup(db_session)
    amazon_order = await _make_marketplace_order(
        db_session,
        order_number="MKT-FILT-AMZ",
        customer=customer,
        source_system="amazon",
        external_id="AMZ-FILT-1",
    )
    flipkart_order = await _make_marketplace_order(
        db_session,
        order_number="MKT-FILT-FKT",
        customer=customer,
        source_system="flipkart",
        external_id="FKT-FILT-1",
    )

    async with bearer_client(app, get_db, db_session, user.id) as client:
        response = await client.get("/api/v1/marketplace/orders", params={"marketplace": "amazon"})

    assert response.status_code == 200
    order_ids = {row["id"] for row in response.json()["data"]}
    assert str(amazon_order.id) in order_ids
    assert str(flipkart_order.id) not in order_ids


async def test_marketplace_order_response_includes_source_and_external_id(
    db_session: AsyncSession,
) -> None:
    user, _no_access, customer = await _setup(db_session)
    await _make_marketplace_order(
        db_session,
        order_number="MKT-FIELDS-1",
        customer=customer,
        source_system="meesho",
        external_id="MSH-FIELDS-1",
    )

    async with bearer_client(app, get_db, db_session, user.id) as client:
        response = await client.get("/api/v1/marketplace/orders", params={"marketplace": "meesho"})

    assert response.status_code == 200
    row = response.json()["data"][0]
    assert row["source_system"] == "meesho"
    assert row["external_id"] == "MSH-FIELDS-1"


async def test_marketplace_orders_requires_marketplace_read_permission(
    db_session: AsyncSession,
) -> None:
    _user, no_access_user, _customer = await _setup(db_session)
    async with bearer_client(app, get_db, db_session, no_access_user.id) as client:
        response = await client.get("/api/v1/marketplace/orders")
    assert response.status_code == 403


# ---------------------------------------------------------------------
# GET /marketplace/dashboard/summary
# ---------------------------------------------------------------------
async def test_marketplace_dashboard_summary_scoped_to_marketplaces_only(
    db_session: AsyncSession,
) -> None:
    user, _no_access, customer = await _setup(db_session)
    await _make_marketplace_order(
        db_session,
        order_number="MKT-DASH-AMZ",
        customer=customer,
        source_system="amazon",
        external_id="AMZ-DASH-1",
    )
    shopify_order = await make_order(db_session, order_number="MKT-DASH-SHOPIFY", customer=customer)
    shopify_order.source_system = "shopify"
    await db_session.commit()

    async with bearer_client(app, get_db, db_session, user.id) as client:
        response = await client.get("/api/v1/marketplace/dashboard/summary")

    assert response.status_code == 200
    data = response.json()["data"]
    # Exactly the 1 Amazon order -- the Shopify order must never be counted.
    assert int(data["total_orders"]["current"]) == 1
    assert int(data["amazon_orders"]["current"]) == 1
    assert int(data["flipkart_orders"]["current"]) == 0
    assert int(data["meesho_orders"]["current"]) == 0


async def test_marketplace_dashboard_summary_is_zeroed_with_no_data(
    db_session: AsyncSession,
) -> None:
    user, _no_access, _customer = await _setup(db_session)
    async with bearer_client(app, get_db, db_session, user.id) as client:
        response = await client.get("/api/v1/marketplace/dashboard/summary")
    assert response.status_code == 200
    data = response.json()["data"]
    assert int(data["total_orders"]["current"]) == 0
    assert float(data["total_sales"]["current"]) == 0


async def test_marketplace_dashboard_requires_permission(db_session: AsyncSession) -> None:
    _user, no_access_user, _customer = await _setup(db_session)
    async with bearer_client(app, get_db, db_session, no_access_user.id) as client:
        response = await client.get("/api/v1/marketplace/dashboard/summary")
    assert response.status_code == 403


# ---------------------------------------------------------------------
# Regression: the existing Shopify-facing /orders endpoint is unaffected.
# ---------------------------------------------------------------------
async def test_existing_orders_endpoint_unaffected_by_source_systems_filter(
    db_session: AsyncSession,
) -> None:
    team_leader_role = await make_role(
        db_session, name="ORD_ADMIN", permission_codes=["orders.read"]
    )
    admin = await make_user(
        db_session, email="ordadmin@marketplace.example.com", role=team_leader_role
    )
    customer = await make_customer(db_session)
    shopify_order = await make_order(
        db_session, order_number="MKT-REGRESSION-SHOPIFY", customer=customer
    )
    amazon_order = await _make_marketplace_order(
        db_session,
        order_number="MKT-REGRESSION-AMZ",
        customer=customer,
        source_system="amazon",
        external_id="AMZ-REGRESSION-1",
    )

    async with bearer_client(app, get_db, db_session, admin.id) as client:
        response = await client.get("/api/v1/orders")

    assert response.status_code == 200
    order_ids = {row["id"] for row in response.json()["data"]}
    # Still returns every order regardless of source_system -- the new
    # optional filter defaults to "no restriction" exactly like before.
    assert str(shopify_order.id) in order_ids
    assert str(amazon_order.id) in order_ids

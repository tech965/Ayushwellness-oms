"""Flipkart Seller API order sync -- all Flipkart traffic is mocked
(httpx.MockTransport). No real credentials exist for this account yet
(registration previously failed with HTTP 500 at Seller Dashboard >
Developer Access), so these tests can only verify the client/service
logic against Flipkart's documented request/response shapes -- they
cannot confirm the real account's credentials or permissions work.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import httpx
import pytest
from app.integrations.flipkart.client import FlipkartClient
from app.integrations.flipkart.config import FlipkartConfig
from app.integrations.flipkart.errors import (
    FlipkartAuthError,
    FlipkartNotConfiguredError,
    FlipkartPermissionError,
    FlipkartRateLimitError,
)
from app.services.flipkart_sales_service import (
    FlipkartSalesService,
    dispatch_type_for_status,
)

pytestmark = pytest.mark.asyncio

CFG = FlipkartConfig(app_id="app123", app_secret="secret456")
DAY1 = date.today() - timedelta(days=3)
DAY2 = DAY1 + timedelta(days=1)


def shipment(order_id="OD1", sku="SKU-A", fsn="FSN1", qty=1, price="499.00", status="APPROVED"):
    return {
        "shipmentId": "SHIP1",
        "subShipments": [{"packageTitle": "Ashwagandha Capsules"}],
        "orderItems": [
            {
                "orderId": order_id,
                "orderDate": "2026-11-01T10:00:00",
                "status": status,
                "sku": sku,
                "fsn": fsn,
                "quantity": qty,
                "priceComponents": {"sellingPrice": price},
            }
        ],
    }


class FakeFlipkart:
    """Stateful mock of the OAuth token endpoint + Search Shipments API."""

    def __init__(self, *, token_status=200, api_status=200, shipments=None, next_page_url=None):
        self.token_status = token_status
        self.api_status = api_status
        self.shipments = shipments if shipments is not None else [shipment()]
        self.next_page_url = next_page_url
        self.calls: list[str] = []
        self.request_bodies: list[dict] = []

    def handler(self, req: httpx.Request) -> httpx.Response:
        self.calls.append(f"{req.method} {req.url.path}")
        if "oauth-service/oauth/token" in req.url.path:
            if self.token_status != 200:
                return httpx.Response(self.token_status, json={"error": "invalid_client"})
            return httpx.Response(
                200, json={"access_token": "tok", "token_type": "bearer", "expires_in": 3600}
            )
        assert req.headers["Authorization"] == "Bearer tok"
        if self.api_status != 200:
            return httpx.Response(self.api_status, json={"error": "Access denied"})
        if req.url.path.endswith("/sellers/v3/shipments/filter/"):
            if req.method == "POST":
                import json as jsonlib

                self.request_bodies.append(jsonlib.loads(req.content))
            return httpx.Response(
                200,
                json={
                    "shipments": self.shipments,
                    "nextPageUrl": self.next_page_url,
                    "hasMore": bool(self.next_page_url),
                },
            )
        return httpx.Response(404, json={"error": "not found"})


def make_service(fake: FakeFlipkart, cfg=CFG) -> FlipkartSalesService:
    async def nosleep(_):
        return None

    http = httpx.AsyncClient(transport=httpx.MockTransport(fake.handler))
    client = FlipkartClient(cfg, http_client=http, sleeper=nosleep)
    return FlipkartSalesService(cfg, client)


# --- status / not-configured -----------------------------------------------


async def test_status_reports_not_configured_when_credentials_missing():
    svc = FlipkartSalesService(FlipkartConfig(app_id=None, app_secret=None))
    st = svc.status()
    assert st.configured is False
    assert "FLIPKART_APP_ID" in st.missing
    assert "FLIPKART_APP_SECRET" in st.missing


async def test_orders_page_raises_not_configured_never_returns_demo_data():
    svc = FlipkartSalesService(FlipkartConfig(app_id=None, app_secret=None))
    with pytest.raises(FlipkartNotConfiguredError):
        await svc.orders_page(
            date_from=DAY1, date_to=DAY2, status=None, page_size=20, next_page_url=None
        )


# --- authentication ----------------------------------------------------


async def test_authentication_failure_is_clear():
    svc = make_service(FakeFlipkart(token_status=400))
    with pytest.raises(FlipkartAuthError):
        await svc.orders_page(
            date_from=DAY1, date_to=DAY2, status=None, page_size=20, next_page_url=None
        )


async def test_permission_denied_is_clear():
    svc = make_service(FakeFlipkart(api_status=403))
    with pytest.raises(FlipkartPermissionError):
        await svc.orders_page(
            date_from=DAY1, date_to=DAY2, status=None, page_size=20, next_page_url=None
        )


async def test_rate_limit_retries_then_raises():
    class AlwaysRateLimited(FakeFlipkart):
        def handler(self, req):
            if "oauth-service/oauth/token" in req.url.path:
                return super().handler(req)
            return httpx.Response(429, headers={"Retry-After": "0"}, json={})

    svc = make_service(AlwaysRateLimited())
    with pytest.raises(FlipkartRateLimitError):
        await svc.orders_page(
            date_from=DAY1, date_to=DAY2, status=None, page_size=20, next_page_url=None
        )


# --- order parsing / aggregation ----------------------------------------


async def test_orders_page_parses_documented_fields():
    svc = make_service(FakeFlipkart())
    page = await svc.orders_page(
        date_from=DAY1, date_to=DAY2, status=None, page_size=20, next_page_url=None
    )
    assert len(page.items) == 1
    item = page.items[0]
    assert item.order_id == "OD1"
    assert item.sku == "SKU-A"
    assert item.fsn == "FSN1"
    assert item.quantity == 1
    assert item.selling_price == Decimal("499.00")
    assert item.product_name == "Ashwagandha Capsules"
    # Never fabricated -- these have no documented source field.
    assert item.payment_type is None
    assert item.tracking_id is None
    assert "payment_type" in page.unavailable
    assert "tracking_id" in page.unavailable


async def test_orders_page_sends_documented_filter_shape():
    fake = FakeFlipkart()
    svc = make_service(fake)
    await svc.orders_page(
        date_from=DAY1, date_to=DAY2, status="SHIPPED", page_size=20, next_page_url=None
    )
    body = fake.request_bodies[0]
    assert body["filter"]["type"] == "postDispatch"
    assert body["filter"]["states"] == ["SHIPPED"]
    assert "orderDate" in body["filter"]
    assert body["pagination"]["pageSize"] == 20


async def test_dispatch_type_mapping_matches_documented_states():
    assert dispatch_type_for_status("APPROVED")[0] == "preDispatch"
    assert dispatch_type_for_status("SHIPPED")[0] == "postDispatch"
    assert dispatch_type_for_status("CANCELLED")[0] == "cancelled"
    assert dispatch_type_for_status(None)[0] == "preDispatch"
    with pytest.raises(ValueError):
        dispatch_type_for_status("NOT_A_REAL_STATUS")


async def test_pagination_passes_next_page_url_through_unchanged():
    fake = FakeFlipkart()
    svc = make_service(fake)
    await svc.orders_page(
        date_from=None,
        date_to=None,
        status=None,
        page_size=20,
        next_page_url="https://api.flipkart.net/sellers/v3/shipments/filter/?page=2",
    )
    assert fake.calls[-1] == "GET /sellers/v3/shipments/filter/"


async def test_export_walks_all_pages():
    fake = FakeFlipkart(next_page_url="https://api.flipkart.net/sellers/v3/shipments/filter/?p=2")
    # Second call (via next_page_url) returns no further next page.
    call_count = {"n": 0}
    orig_handler = fake.handler

    def handler(req):
        if "shipments/filter" in req.url.path and req.method == "GET":
            call_count["n"] += 1
            return httpx.Response(
                200,
                json={
                    "shipments": [shipment(order_id="OD2")],
                    "nextPageUrl": None,
                    "hasMore": False,
                },
            )
        return orig_handler(req)

    fake.handler = handler
    svc = make_service(fake)
    page = await svc.all_orders_for_export(date_from=DAY1, date_to=DAY2, status=None)
    assert len(page.items) == 2
    assert page.has_more is False
    assert call_count["n"] == 1


async def test_export_stops_within_time_budget():
    import app.services.flipkart_sales_service as svc_module

    fake = FakeFlipkart(next_page_url="https://api.flipkart.net/sellers/v3/shipments/filter/?p=2")
    svc = make_service(fake)
    old_budget = svc_module.EXPORT_TIME_BUDGET_SECONDS
    svc_module.EXPORT_TIME_BUDGET_SECONDS = -1.0
    try:
        page = await svc.all_orders_for_export(date_from=DAY1, date_to=DAY2, status=None)
    finally:
        svc_module.EXPORT_TIME_BUDGET_SECONDS = old_budget
    assert any("time budget reached" in w for w in page.warnings)

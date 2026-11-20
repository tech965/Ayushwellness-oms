"""COD reconciliation under SHOPIFY_LIVE_MODE (orders read live from Shopify,
AWBs from `shipment_refs`, remittance from `cod_remittances`). Shopify is
mocked; nothing here is verified against a real store."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from app.core.config import settings
from app.integrations.registry import clear_adapters, register_adapter
from app.integrations.shopify.adapter import ShopifyAdapter
from app.models.cod_remittance import CodRemittance
from app.models.order_workflow import OrderWorkflow
from app.models.shipment_ref import ShipmentRef
from app.services.cod_reconciliation_service import CodReconciliationService
from app.services.order_ref import encode_shopify_order_ref
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.test_live_orders_workflow import _node, _page
from tests.test_shopify_live_mode import _StubClient

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _live(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "SHOPIFY_LIVE_MODE", True)
    yield
    clear_adapters()


def _install(*pages: dict) -> None:
    register_adapter(ShopifyAdapter(client=_StubClient(list(pages))))


async def _link_awb(session: AsyncSession, external_id: str, awb: str) -> None:
    wf = OrderWorkflow(id=uuid.uuid4(), external_order_id=external_id)
    session.add(wf)
    await session.flush()
    session.add(ShipmentRef(order_id=wf.id, awb=awb, shiprocket_shipment_id=f"S{awb}"))
    await session.commit()


async def test_live_summary_rows_and_remittance_join(db_session: AsyncSession) -> None:
    # 1 = COD (shipped, AWB A1), 2 = prepaid, 3 = COD (not shipped)
    nodes = [
        _node(1, gateway="Cash on Delivery (COD)", financial="PENDING"),
        _node(2),
        _node(3, gateway="Cash on Delivery (COD)", financial="PENDING"),
    ]
    await _link_awb(db_session, "1", "A1")
    db_session.add(
        CodRemittance(awb="A1", collected_amount=Decimal("499"), remitted_amount=Decimal("100"))
    )
    await db_session.commit()
    _install(_page(nodes), _page(nodes), _page(nodes))
    svc = CodReconciliationService(db_session)

    s = await svc.summary(date_from=None, date_to=None)
    assert s.data_source == "live" and s.complete
    assert s.cod.total.orders == 2 and s.prepaid.total.orders == 1
    assert s.total_revenue == Decimal("1497.00")
    assert s.cod_financials.shipped_orders == 1  # only the COD order with an AWB
    assert s.cod_financials.expected_amount == Decimal("499.00")
    assert s.cod_financials.collected_amount == Decimal("499")
    assert s.cod_financials.pending_remittance == Decimal("399")
    assert s.cod_financials.bank_verified_amount == 0

    rows, total = await svc.list_orders(page=1, page_size=10, payment_type="cod")
    assert total == 2
    shipped = next(r for r in rows if r.awb == "A1")
    assert shipped.financial_data == "reported" and shipped.pending_remittance == Decimal("399")
    assert shipped.tracking_url.endswith("/A1")
    unshipped = next(r for r in rows if r.awb is None)
    assert unshipped.financial_data == "no_data" and unshipped.collected_amount is None

    # live reconciliation reads/writes nothing in provider tables
    assert (await db_session.execute(select(func.count()).select_from(ShipmentRef))).scalar() == 1


async def test_live_search_by_awb_finds_order_shopify_cannot_search(
    db_session: AsyncSession,
) -> None:
    await _link_awb(db_session, "1", "AWB-XYZ")
    node = _node(1, gateway="Cash on Delivery (COD)", financial="PENDING")
    # first page: Shopify q-search finds nothing; second: get_orders_by_ids
    _install(_page([]), _page([node]))
    rows, total = await CodReconciliationService(db_session).list_orders(
        page=1, page_size=10, payment_type="cod", q="XYZ"
    )
    assert total == 1 and rows[0].awb == "AWB-XYZ"
    assert rows[0].id != encode_shopify_order_ref("1")  # keeps the workflow ref id


async def test_live_http_endpoints_work_and_export(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    nodes = [_node(1, gateway="Cash on Delivery (COD)", financial="PENDING")]
    _install(_page(nodes), _page(nodes), _page(nodes))
    async with await make_authenticated_client(
        db_session, permission_codes=["analytics.read"], email="l@example.com"
    ) as c:
        assert (await c.get("/api/v1/revenue-reconciliation/summary")).status_code == 200
        r = await c.get("/api/v1/revenue-reconciliation/orders?payment_type=cod")
        assert r.status_code == 200 and r.json()["meta"]["total_items"] == 1
        x = await c.get("/api/v1/revenue-reconciliation/orders/export?payment_type=cod")
        assert x.status_code == 200 and x.content[:2] == b"PK"
        # probe is admin-only
        assert (await c.get("/api/v1/revenue-reconciliation/shiprocket-probe")).status_code == 403
    assert datetime.now(UTC)  # keep import used

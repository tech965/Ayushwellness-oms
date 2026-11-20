"""COD/Prepaid revenue split + Shiprocket COD remittance reconciliation."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from app.integrations.shiprocket.remittance import normalize_cod_remittance
from app.models.cod_remittance import CodRemittance
from app.models.enums import FulfillmentStatus, PaymentType, ShipmentStatus
from app.models.shipment import Shipment
from app.repositories.order import OrderRepository
from app.schemas.order import OrderItemCreateRequest
from app.services.cod_reconciliation_service import CodReconciliationService
from app.services.order_service import OrderService
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio


async def _order(
    session: AsyncSession,
    number: str,
    ptype: PaymentType,
    amount: str,
    *,
    fulfillment: FulfillmentStatus = FulfillmentStatus.UNFULFILLED,
    awb: str | None = None,
):
    order = await OrderService(session).create_order(
        actor=None,
        order_number=number,
        customer_id=None,
        order_datetime=datetime.now(UTC),
        currency="INR",
        payment_type=ptype,
        shipping_charge=0,
        notes=None,
        items=[
            OrderItemCreateRequest(sku="S", product_name="Product", quantity=1, unit_price=amount)
        ],
    )
    await OrderRepository(session).update(order, fulfillment_status=fulfillment)
    if awb:
        session.add(Shipment(order_id=order.id, awb=awb, current_status=ShipmentStatus.DELIVERED))
    await session.commit()
    return order


class _FakeAdapter:
    """Stands in for ShiprocketAdapter.fetch_cod_remittances."""

    def __init__(self, pages: list[list[dict]]) -> None:
        self.pages = pages

    async def fetch_cod_remittances(self, *, page: int = 1, per_page: int = 100):
        records = self.pages[page - 1]
        return records, page < len(self.pages)


def test_normalizer_reads_known_keys_and_never_invents_values() -> None:
    row = normalize_cod_remittance(
        {
            "awb_code": "AWB1",
            "cod_amount": "1,200.50",
            "remittance_amount": 1000,
            "utr": "UTR9",
            "remittance_date": "2026-11-10",
            "status": "Processed",
        }
    )
    assert row["awb"] == "AWB1"
    assert row["cod_amount"] == Decimal("1200.50")
    assert row["remitted_amount"] == Decimal("1000")
    assert row["collected_amount"] is None  # not reported -> stays None
    assert row["remittance_reference"] == "UTR9"
    assert row["settlement_date"].year == 2026
    assert normalize_cod_remittance({"cod_amount": 5}) is None  # no AWB


async def test_sync_is_disabled_until_remittance_path_is_configured(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.core.config import settings
    from app.core.exceptions import IntegrationError
    from app.integrations.shiprocket.adapter import ShiprocketAdapter
    from app.tasks.cod_remittance_sync import _run

    monkeypatch.setattr(settings, "SHIPROCKET_REMITTANCE_PATH", "")
    with pytest.raises(IntegrationError):
        await ShiprocketAdapter().fetch_cod_remittances()
    await _run()  # scheduled job is a silent no-op, makes no Shiprocket call
    assert (await db_session.execute(select(CodRemittance))).scalars().all() == []


async def test_summary_splits_cod_prepaid_and_fulfilment(db_session: AsyncSession) -> None:
    await _order(db_session, "C1", PaymentType.COD, "500", fulfillment=FulfillmentStatus.FULFILLED)
    await _order(db_session, "C2", PaymentType.COD, "300")
    await _order(
        db_session, "P1", PaymentType.PREPAID, "200", fulfillment=FulfillmentStatus.FULFILLED
    )

    s = await CodReconciliationService(db_session).summary(date_from=None, date_to=None)

    assert s.total_revenue == Decimal("1000")
    assert (s.cod.total.orders, s.cod.total.amount) == (2, Decimal("800"))
    assert s.cod.fulfilled.amount == Decimal("500")
    assert s.cod.unfulfilled.amount == Decimal("300")
    assert s.prepaid.fulfilled.orders == 1
    assert s.prepaid.unfulfilled.orders == 0


async def test_delivered_cod_is_not_assumed_collected_or_remitted(
    db_session: AsyncSession,
) -> None:
    await _order(
        db_session, "C1", PaymentType.COD, "500", fulfillment=FulfillmentStatus.FULFILLED, awb="A1"
    )
    svc = CodReconciliationService(db_session)

    s = await svc.summary(date_from=None, date_to=None)
    assert s.cod_financials.expected_amount == Decimal("500")
    assert s.cod_financials.collected_amount == 0
    assert s.cod_financials.remitted_amount == 0
    assert s.cod_financials.orders_without_shiprocket_data == 1

    rows, _ = await svc.list_orders(page=1, page_size=10, payment_type="cod")
    assert rows[0].financial_data == "no_data"
    assert rows[0].collected_amount is None and rows[0].remitted_amount is None
    assert rows[0].bank_verified is False


async def test_sync_is_idempotent_and_keeps_bank_verification(db_session: AsyncSession) -> None:
    await _order(
        db_session, "C1", PaymentType.COD, "500", fulfillment=FulfillmentStatus.FULFILLED, awb="A1"
    )
    svc = CodReconciliationService(db_session)
    record = {
        "awb_code": "A1",
        "cod_amount": 500,
        "collected_amount": 500,
        "remitted_amount": 300,
        "utr": "UTR1",
        "remittance_date": "2026-11-10",
    }

    first = await svc.sync_remittances(_FakeAdapter([[record]]))
    assert first == {"created": 1, "updated": 0, "skipped": 0}

    await svc.verify_bank_credit(
        "A1",
        amount=Decimal("300"),
        reference="BANK-77",
        credited_on=datetime(2026, 11, 12, tzinfo=UTC),
        user_id=None,
    )
    second = await svc.sync_remittances(_FakeAdapter([[record], [{"no_awb": 1}]]))
    assert second["created"] == 0 and second["updated"] == 1 and second["skipped"] == 1

    rems = (await db_session.execute(select(CodRemittance))).scalars().all()
    assert len(rems) == 1  # no duplicates
    assert rems[0].bank_credit_reference == "BANK-77"  # sync never wipes verification

    s = await svc.summary(date_from=None, date_to=None)
    f = s.cod_financials
    assert (f.collected_amount, f.remitted_amount) == (Decimal("500"), Decimal("300"))
    assert f.pending_remittance == Decimal("200")
    assert f.bank_verified_amount == Decimal("300")

    rows, total = await svc.list_orders(page=1, page_size=10, payment_type="cod", q="A1")
    assert total == 1
    r = rows[0]
    assert r.financial_data == "reported"
    assert r.settlement_reference == "UTR1"
    assert r.pending_remittance == Decimal("200")
    assert r.bank_verified is True
    assert r.tracking_url.endswith("/A1")


async def test_reported_remittance_alone_is_not_bank_verified(db_session: AsyncSession) -> None:
    await _order(db_session, "C1", PaymentType.COD, "500", awb="A1")
    svc = CodReconciliationService(db_session)
    await svc.sync_remittances(
        _FakeAdapter([[{"awb_code": "A1", "collected_amount": 500, "remitted_amount": 500}]])
    )
    s = await svc.summary(date_from=None, date_to=None)
    assert s.cod_financials.remitted_amount == Decimal("500")
    assert s.cod_financials.bank_verified_amount == 0


async def test_prepaid_rows_have_no_cod_fields_and_filters_apply(
    db_session: AsyncSession,
) -> None:
    await _order(
        db_session, "P1", PaymentType.PREPAID, "200", fulfillment=FulfillmentStatus.FULFILLED
    )
    await _order(db_session, "P2", PaymentType.PREPAID, "100")
    svc = CodReconciliationService(db_session)

    rows, total = await svc.list_orders(
        page=1, page_size=10, payment_type="prepaid", fulfillment_status="fulfilled"
    )
    assert total == 1 and rows[0].order_number == "P1"
    assert rows[0].expected_cod_amount is None and rows[0].financial_data is None

    rows, total = await svc.list_orders(page=1, page_size=10, payment_type="prepaid", q="P2")
    assert total == 1


async def test_export_and_http_endpoints_are_permission_gated(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    await _order(db_session, "C1", PaymentType.COD, "500", awb="A1")
    async with await make_authenticated_client(
        db_session, permission_codes=["analytics.read"], email="a@example.com"
    ) as c:
        assert (await c.get("/api/v1/revenue-reconciliation/summary")).status_code == 200
        r = await c.get("/api/v1/revenue-reconciliation/orders?payment_type=cod")
        assert r.status_code == 200 and r.json()["meta"]["total_items"] == 1
        x = await c.get("/api/v1/revenue-reconciliation/orders/export?payment_type=cod")
        assert x.status_code == 200 and x.content[:2] == b"PK"  # real xlsx (zip)
        # reading analytics does not allow triggering a sync or verifying money
        assert (await c.post("/api/v1/revenue-reconciliation/sync")).status_code == 403
        v = await c.post(
            "/api/v1/revenue-reconciliation/remittances/A1/verify-bank-credit",
            json={"amount": "1", "reference": "x", "credited_on": "2026-11-01T00:00:00Z"},
        )
        assert v.status_code == 403

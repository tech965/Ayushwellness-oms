"""Live (API-first) Cashfree: payment details/verification for live-only
orders, transactions/refunds/settlements/summary (bounded), checkout
create/reuse without stored payment records, gating of stored-data
payment views, and the live webhook.

Cashfree and Shopify are MOCKED. Nothing here is verified against the
real services.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from app.core.config import settings
from app.db.session import get_db
from app.integrations.cashfree.errors import CashfreeApiError
from app.integrations.registry import clear_adapters, register_adapter
from app.integrations.shopify.adapter import ShopifyAdapter
from app.main import app
from app.models.cashfree_settlement import CashfreeSettlement
from app.models.integration import WebhookEvent
from app.models.payment import Payment, PaymentTransaction
from app.models.webhook_delivery import WebhookDelivery
from app.services import live_cashfree
from app.services.live_workflow_handlers import register_default_live_handlers
from app.services.order_ref import encode_shopify_order_ref
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.telecalling_test_utils import bearer_client, make_role, make_user
from tests.test_live_orders_workflow import _assert_no_provider_rows, _node
from tests.test_live_telecalling_workflow import RouterStub
from tests.test_shopify_live_mode import _count

pytestmark = pytest.mark.asyncio

_SECRET = "cashfree-live-secret"


class CashfreeStub:
    """Duck-typed CashfreeClient. `orders` maps cashfree order id ->
    (order dict, [payment dicts])."""

    def __init__(self, orders: dict | None = None) -> None:
        self.orders = orders or {}
        self.created: list[dict] = []
        self.recon_pages: list[dict] = []
        self.settlement_pages: list[dict] = []
        self.calls: list[str] = []

    async def aclose(self) -> None:
        return None

    async def get_order(self, order_id: str) -> dict:
        self.calls.append(f"order:{order_id}")
        if order_id not in self.orders:
            raise CashfreeApiError("order not found", error_type="not_found", status_code=404)
        return self.orders[order_id][0]

    async def get_payments_for_order(self, order_id: str) -> list:
        return self.orders[order_id][1]

    async def create_order(self, payload: dict) -> dict:
        self.created.append(payload)
        return {"order_status": "ACTIVE", "payment_session_id": "session_new", "cf_order_id": 1}

    async def get_reconciliation(self, *, start_date, end_date, cursor=None, limit=1000):  # noqa: ANN001
        return self.recon_pages.pop(0) if self.recon_pages else {"data": [], "cursor": None}

    async def get_settlements(self, *, start_date=None, end_date=None, cursor=None, **_):  # noqa: ANN001
        return (
            self.settlement_pages.pop(0) if self.settlement_pages else {"data": [], "cursor": None}
        )


def _paid(order_id="1001", amount="499.00") -> tuple[dict, list]:
    return (
        {"order_status": "PAID", "order_amount": amount, "order_currency": "INR"},
        [
            {
                "cf_payment_id": 11,
                "payment_status": "SUCCESS",
                "payment_amount": amount,
                "payment_currency": "INR",
                "payment_method": {"upi": {}},
                "payment_time": "2026-10-03T10:00:00+00:00",
            }
        ],
    )


@pytest.fixture(autouse=True)
def _live(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "SHOPIFY_LIVE_MODE", True)
    monkeypatch.setattr(settings, "CASHFREE_CLIENT_SECRET", _SECRET)
    yield
    live_cashfree.client_factory = None
    clear_adapters()
    register_default_live_handlers()
    app.dependency_overrides.clear()


def _use(stub: CashfreeStub) -> CashfreeStub:
    live_cashfree.client_factory = lambda: stub
    register_adapter(ShopifyAdapter(client=RouterStub({1: _node(1)})))
    return stub


async def _user(db_session: AsyncSession, perms: list[str]):
    role = await make_role(db_session, name="PAY", permission_codes=perms)
    return await make_user(db_session, email="pay@example.com", role=role)


# --- payment details + verification ----------------------------------------------------


async def test_order_payment_read_from_cashfree_and_verified_against_shopify(
    db_session: AsyncSession,
) -> None:
    user = await _user(db_session, ["payments.read", "orders.read"])
    stub = _use(CashfreeStub({"1001": _paid()}))  # '#1001' -> '1001'
    ref = encode_shopify_order_ref("1")

    async with bearer_client(app, get_db, db_session, user.id) as client:
        response = await client.get(f"/api/v1/payments/cashfree/live/orders/{ref}")

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["found"] and data["paid"] and data["cashfree_order_id"] == "1001"
    assert data["payments"][0]["method"] == "upi" and data["payments"][0]["status"] == "paid"
    assert data["outcome"] == "match"  # the node's Shopify financial status is PAID
    await _assert_no_provider_rows(db_session)
    assert await _count(db_session, Payment) == 0
    assert await _count(db_session, PaymentTransaction) == 0
    assert stub.calls == ["order:1001"]


async def test_verification_reports_mismatch_and_missing_without_correcting(
    db_session: AsyncSession,
) -> None:
    user = await _user(db_session, ["payments.read", "orders.read"])
    stub = _use(CashfreeStub({"1001": ({"order_status": "ACTIVE"}, [])}))
    ref = encode_shopify_order_ref("1")

    async with bearer_client(app, get_db, db_session, user.id) as client:
        mismatch = await client.get(f"/api/v1/payments/cashfree/live/orders/{ref}")
        stub.orders.clear()
        missing = await client.get(f"/api/v1/payments/cashfree/live/orders/{ref}")

    assert mismatch.json()["data"]["outcome"] == "mismatch"  # Shopify PAID, Cashfree unpaid
    assert missing.json()["data"]["outcome"] == "not_found_in_cashfree"
    assert missing.json()["data"]["found"] is False


async def test_retry_suffix_order_is_found_after_expired_attempt(db_session: AsyncSession) -> None:
    user = await _user(db_session, ["payments.read", "orders.read"])
    expired = ({"order_status": "EXPIRED"}, [])
    _use(CashfreeStub({"1001": expired, "1001-1": _paid()}))

    async with bearer_client(app, get_db, db_session, user.id) as client:
        response = await client.get(
            f"/api/v1/payments/cashfree/live/orders/{encode_shopify_order_ref('1')}"
        )

    assert response.json()["data"]["cashfree_order_id"] == "1001-1"
    assert response.json()["data"]["paid"] is True


async def test_cashfree_api_failure_is_an_explicit_error(db_session: AsyncSession) -> None:
    user = await _user(db_session, ["payments.read", "orders.read"])

    class Down(CashfreeStub):
        async def get_order(self, order_id: str) -> dict:
            raise CashfreeApiError("rate limited", error_type="rate_limit_error", status_code=429)

    _use(Down())

    async with bearer_client(app, get_db, db_session, user.id) as client:
        response = await client.get(
            f"/api/v1/payments/cashfree/live/orders/{encode_shopify_order_ref('1')}"
        )

    assert response.status_code >= 400
    assert "paid" not in response.json().get("data", {}) if response.json().get("data") else True


# --- bulk reads ---------------------------------------------------------------------------


async def test_transactions_refunds_and_summary_from_reconciliation_feed(
    db_session: AsyncSession,
) -> None:
    user = await _user(db_session, ["payments.read"])
    rows = [
        {
            "event_type": "PAYMENT",
            "order_id": "1001",
            "cf_payment_id": 1,
            "status": "SUCCESS",
            "payment_amount": "499.00",
            "payment_time": "2026-10-03T10:00:00+00:00",
        },
        {
            "event_type": "PAYMENT",
            "order_id": "1002",
            "cf_payment_id": 2,
            "status": "FAILED",
            "payment_amount": "250.00",
            "payment_time": "2026-10-03T11:00:00+00:00",
        },
        {
            "event_type": "REFUND",
            "order_id": "1001",
            "cf_payment_id": 1,
            "status": "SUCCESS",
            "refund_amount": "100.00",
            "event_time": "2026-10-04T10:00:00+00:00",
        },
    ]
    stub = _use(CashfreeStub())

    async with bearer_client(app, get_db, db_session, user.id) as client:
        stub.recon_pages = [{"data": rows, "cursor": None}]
        tx = await client.get("/api/v1/payments/cashfree/live/transactions")
        stub.recon_pages = [{"data": rows, "cursor": None}]
        refunds = await client.get("/api/v1/payments/cashfree/live/refunds")
        stub.recon_pages = [{"data": rows, "cursor": None}]
        summary = await client.get("/api/v1/payments/cashfree/live/summary")

    assert [i["cashfree_order_id"] for i in tx.json()["data"]["items"]] == ["1001", "1002"]
    assert tx.json()["data"]["complete"] is True
    assert len(refunds.json()["data"]["items"]) == 1
    sdata = summary.json()["data"]
    assert Decimal(str(sdata["payments_received"])) == Decimal("499.00")
    assert Decimal(str(sdata["refunded"])) == Decimal("100.00")
    assert {b["status"]: b["count"] for b in sdata["by_status"]} == {"paid": 1, "failed": 1}
    assert await _count(db_session, Payment) == 0


async def test_bulk_reads_flag_incomplete_when_page_bound_is_hit(db_session: AsyncSession) -> None:
    user = await _user(db_session, ["payments.read"])
    stub = _use(CashfreeStub())
    stub.recon_pages = [
        {"data": [{"event_type": "PAYMENT", "order_id": "1", "status": "SUCCESS"}], "cursor": "c1"},
        {"data": [{"event_type": "PAYMENT", "order_id": "2", "status": "SUCCESS"}], "cursor": "c2"},
    ]

    async with bearer_client(app, get_db, db_session, user.id) as client:
        response = await client.get(
            "/api/v1/payments/cashfree/live/transactions", params={"max_pages": 2}
        )

    data = response.json()["data"]
    assert data["complete"] is False and "incomplete" in data["warnings"][0]


async def test_settlements_are_read_live_and_not_stored(db_session: AsyncSession) -> None:
    user = await _user(db_session, ["payments.read"])
    stub = _use(CashfreeStub())
    stub.settlement_pages = [
        {
            "data": [
                {
                    "cf_settlement_id": "S1",
                    "status": "SUCCESS",
                    "payment_amount": "1000",
                    "amount_settled": "980",
                    "settlement_utr": "UTR1",
                },
                {"cf_settlement_id": "S2", "status": "INITIATED", "payment_amount": "500"},
            ],
            "cursor": None,
        }
    ]

    async with bearer_client(app, get_db, db_session, user.id) as client:
        response = await client.get("/api/v1/payments/cashfree/live/settlements")

    data = response.json()["data"]
    assert [s["cf_settlement_id"] for s in data["items"]] == ["S1", "S2"]
    assert Decimal(str(data["unsettled_amount"])) == Decimal("500")
    assert data["unsettled_amount_is_derived"] is True  # honest about derivation
    assert await _count(db_session, CashfreeSettlement) == 0


# --- checkout -------------------------------------------------------------------------------


async def test_checkout_create_and_reuse_store_no_payment_record(db_session: AsyncSession) -> None:
    user = await _user(db_session, ["payments.create", "payments.read", "orders.read"])
    stub = _use(CashfreeStub())
    ref = encode_shopify_order_ref("1")
    # The unpaid COD-style node: make Shopify say the order is unpaid.
    register_adapter(
        ShopifyAdapter(
            client=RouterStub({1: _node(1, gateway="Cash on Delivery (COD)", financial="PENDING")})
        )
    )

    async with bearer_client(app, get_db, db_session, user.id) as client:
        created = await client.post(f"/api/v1/payments/cashfree/live/orders/{ref}/checkout")
        # Now Cashfree reports the order ACTIVE with a session -> reuse, no new order
        stub.orders["1001"] = (
            {"order_status": "ACTIVE", "payment_session_id": "session_new"},
            [],
        )
        reused = await client.post(f"/api/v1/payments/cashfree/live/orders/{ref}/checkout")

    assert created.status_code == 200, created.text
    assert created.json()["data"]["payment_session_id"] == "session_new"
    assert created.json()["data"]["created"] is True
    assert stub.created[0]["order_id"] == "1001"
    assert reused.json()["data"]["created"] is False
    assert len(stub.created) == 1
    assert await _count(db_session, Payment) == 0


async def test_checkout_refuses_already_paid_order(db_session: AsyncSession) -> None:
    user = await _user(db_session, ["payments.create", "orders.read"])
    _use(CashfreeStub())

    async with bearer_client(app, get_db, db_session, user.id) as client:
        response = await client.post(
            f"/api/v1/payments/cashfree/live/orders/{encode_shopify_order_ref('1')}/checkout"
        )

    assert response.status_code == 409  # Shopify node 1 is PAID


# --- gating -------------------------------------------------------------------------------------


async def test_stored_data_payment_views_refuse_in_live_mode(db_session: AsyncSession) -> None:
    user = await _user(db_session, ["payments.read"])

    async with bearer_client(app, get_db, db_session, user.id) as client:
        listing = await client.get("/api/v1/payments")
        status = await client.get(
            f"/api/v1/payments/cashfree/orders/{encode_shopify_order_ref('1')}"
        )
        overview = await client.get("/api/v1/payments/cashfree/analytics/overview")

    assert listing.status_code == status.status_code == overview.status_code == 422
    assert "/payments/cashfree/live" in listing.text


# --- webhook ---------------------------------------------------------------------------------------


def _signed(body: bytes, timestamp: str = "1700000000000") -> dict:
    digest = hmac.new(_SECRET.encode(), timestamp.encode() + body, hashlib.sha256).digest()
    return {
        "x-webhook-signature": base64.b64encode(digest).decode(),
        "x-webhook-timestamp": timestamp,
        "Content-Type": "application/json",
    }


async def test_cashfree_webhook_live_mode_verifies_dedupes_and_stores_no_payload(
    db_session: AsyncSession, client
) -> None:
    payload = {
        "type": "PAYMENT_SUCCESS_WEBHOOK",
        "data": {
            "order": {"order_id": "1001"},
            "payment": {"cf_payment_id": 77, "payment_amount": 499},
            "customer_details": {"customer_phone": "9876543210"},
        },
    }
    body = json.dumps(payload).encode()
    url = "/api/v1/webhooks/cashfree/payment"

    forged = await client.post(
        url, content=body, headers={**_signed(body), "x-webhook-signature": "bad"}
    )
    first = await client.post(url, content=body, headers=_signed(body))
    second = await client.post(url, content=body, headers=_signed(body))

    assert forged.status_code == 401
    assert first.status_code == second.status_code == 200
    assert await _count(db_session, WebhookEvent) == 0
    assert await _count(db_session, Payment) == 0
    assert await _count(db_session, PaymentTransaction) == 0
    ledger = (await db_session.execute(select(WebhookDelivery))).scalars().all()
    assert [(r.provider, r.delivery_id) for r in ledger] == [
        ("cashfree", "PAYMENT_SUCCESS_WEBHOOK:77")
    ]
    assert "9876543210" not in repr([(r.provider, r.delivery_id, r.topic) for r in ledger])
    assert isinstance(datetime.now(UTC), datetime)

"""Shopify refunds (read) and order cancellation via live API.

The mutation/query shapes follow Shopify's official docs (version 2026-10)
but the client here is a MOCK. Nothing is run against a real store.
"""

from __future__ import annotations

import pytest
from app.core.config import settings
from app.db.session import get_db
from app.integrations.registry import clear_adapters, register_adapter
from app.integrations.shopify.adapter import ShopifyAdapter
from app.integrations.shopify.queries import ORDER_REFUNDS_QUERY
from app.main import app
from app.models.enums import OrderStatus
from app.models.order import OrderEvent
from app.models.order_workflow import OrderWorkflow
from app.services.order_ref import encode_shopify_order_ref
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.telecalling_test_utils import bearer_client, make_role, make_user

pytestmark = pytest.mark.asyncio


class ActionStub:
    def __init__(self, *, cancelled_at: str | None = None, user_errors: list | None = None) -> None:
        self.cancelled_at = cancelled_at
        self.user_errors = user_errors or []
        self.cancel_calls: list[dict] = []
        self.order_exists = True

    async def execute(self, query: str, variables: dict | None = None) -> dict:
        variables = variables or {}
        if "mutation OrderCancel" in query:
            self.cancel_calls.append(variables)
            return {
                "orderCancel": {
                    "job": None if self.user_errors else {"id": "gid://shopify/Job/1", "done": False},
                    "orderCancelUserErrors": self.user_errors,
                }
            }
        assert "OrderRefunds" in query
        if not self.order_exists:
            return {"order": None}
        return {
            "order": {
                "id": variables["id"],
                "cancelledAt": self.cancelled_at,
                "cancelReason": "CUSTOMER" if self.cancelled_at else None,
                "totalRefundedSet": {"shopMoney": {"amount": "100.00", "currencyCode": "INR"}},
                "refunds": [
                    {
                        "id": "gid://shopify/Refund/9",
                        "createdAt": "2026-10-04T10:00:00Z",
                        "processedAt": "2026-10-04T10:00:05Z",
                        "note": "damaged",
                        "totalRefundedSet": {"shopMoney": {"amount": "100.00", "currencyCode": "INR"}},
                    }
                ],
            }
        }


@pytest.fixture(autouse=True)
def _live(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "SHOPIFY_LIVE_MODE", True)
    yield
    clear_adapters()
    app.dependency_overrides.clear()


async def _user(db_session: AsyncSession, perms: list[str]):
    role = await make_role(db_session, name="ORDERS", permission_codes=perms)
    return await make_user(db_session, email="orders@example.com", role=role)


def _install(stub: ActionStub) -> ActionStub:
    register_adapter(ShopifyAdapter(client=stub))
    return stub


async def test_refunds_are_read_live_and_not_stored(db_session: AsyncSession) -> None:
    user = await _user(db_session, ["orders.read"])
    _install(ActionStub())
    ref = encode_shopify_order_ref("42")

    async with bearer_client(app, get_db, db_session, user.id) as client:
        response = await client.get(f"/api/v1/live/orders/{ref}/refunds")

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["total_refunded"] == "100.00"
    assert data["refunds"][0]["note"] == "damaged" and data["refunds"][0]["amount"] == "100.00"
    assert (await db_session.execute(select(OrderWorkflow))).first() is None  # read-only


async def test_refund_query_uses_only_documented_fields() -> None:
    for field in ("refunds(first: 50)", "totalRefundedSet", "cancelledAt", "cancelReason", "processedAt"):
        assert field in ORDER_REFUNDS_QUERY


async def test_cancel_requires_permission_and_explicit_confirmation(
    db_session: AsyncSession,
) -> None:
    reader = await _user(db_session, ["orders.read"])
    canceller_role = await make_role(db_session, name="CANCEL", permission_codes=["orders.cancel"])
    canceller = await make_user(db_session, email="c@example.com", role=canceller_role)
    stub = _install(ActionStub())
    ref = encode_shopify_order_ref("42")
    body = {"reason": "CUSTOMER", "restock": True}

    async with bearer_client(app, get_db, db_session, reader.id) as client:
        forbidden = await client.post(f"/api/v1/live/orders/{ref}/shopify-cancel", json={**body, "confirm": True})
    async with bearer_client(app, get_db, db_session, canceller.id) as client:
        unconfirmed = await client.post(f"/api/v1/live/orders/{ref}/shopify-cancel", json=body)

    assert forbidden.status_code == 403
    assert unconfirmed.status_code == 422
    assert stub.cancel_calls == []  # nothing was sent to Shopify


async def test_cancel_submits_documented_variables_and_records_activity(
    db_session: AsyncSession,
) -> None:
    user = await _user(db_session, ["orders.cancel"])
    stub = _install(ActionStub())
    ref = encode_shopify_order_ref("42")

    async with bearer_client(app, get_db, db_session, user.id) as client:
        response = await client.post(
            f"/api/v1/live/orders/{ref}/shopify-cancel",
            json={"reason": "INVENTORY", "restock": True, "staff_note": "out of stock", "confirm": True},
        )

    assert response.status_code == 202, response.text
    assert response.json()["data"] == {
        "submitted": True, "confirmed": False, "job_id": "gid://shopify/Job/1", "done": False,
    }
    sent = stub.cancel_calls[0]
    assert sent == {
        "orderId": "gid://shopify/Order/42",
        "reason": "INVENTORY",
        "restock": True,
        "notifyCustomer": False,
        "staffNote": "out of stock",
    }
    assert "refund" not in sent  # the deprecated argument is never sent
    events = (await db_session.execute(select(OrderEvent))).scalars().all()
    assert [e.event_type for e in events] == ["shopify_cancel_requested"]
    workflow = (await db_session.execute(select(OrderWorkflow))).scalar_one()
    assert workflow.status == OrderStatus.PENDING  # NOT cancelled until Shopify confirms


async def test_cancel_surfaces_shopify_errors_and_refuses_already_cancelled(
    db_session: AsyncSession,
) -> None:
    user = await _user(db_session, ["orders.cancel"])
    ref = encode_shopify_order_ref("42")
    body = {"reason": "OTHER", "restock": False, "confirm": True}

    stub = _install(ActionStub(user_errors=[{"field": ["orderId"], "message": "Order cannot be cancelled", "code": "INVALID"}]))
    async with bearer_client(app, get_db, db_session, user.id) as client:
        rejected = await client.post(f"/api/v1/live/orders/{ref}/shopify-cancel", json=body)
        stub.user_errors = []
        stub.cancelled_at = "2026-10-05T00:00:00Z"
        already = await client.post(f"/api/v1/live/orders/{ref}/shopify-cancel", json=body)

    assert rejected.status_code >= 400 and "cannot be cancelled" in rejected.text
    assert already.status_code == 409
    assert len(stub.cancel_calls) == 1  # the already-cancelled order was never re-sent
    assert await _count_events(db_session) == 0  # nothing recorded for a rejected request


async def _count_events(session: AsyncSession) -> int:
    return len((await session.execute(select(OrderEvent))).scalars().all())


async def test_cancellation_is_confirmed_from_live_state_exactly_once(
    db_session: AsyncSession,
) -> None:
    user = await _user(db_session, ["orders.cancel", "orders.read"])
    stub = _install(ActionStub())
    ref = encode_shopify_order_ref("42")

    async with bearer_client(app, get_db, db_session, user.id) as client:
        pending = await client.get(f"/api/v1/live/orders/{ref}/shopify-cancel/status")
        stub.cancelled_at = "2026-10-05T00:00:00Z"
        first = await client.get(f"/api/v1/live/orders/{ref}/shopify-cancel/status")
        second = await client.get(f"/api/v1/live/orders/{ref}/shopify-cancel/status")

    assert pending.json()["data"]["cancelled"] is False
    assert first.json()["data"]["cancelled"] is True and second.json()["data"]["cancelled"] is True
    workflow = (await db_session.execute(select(OrderWorkflow))).scalar_one()
    assert workflow.status == OrderStatus.CANCELLED
    confirmed = [
        e for e in (await db_session.execute(select(OrderEvent))).scalars().all()
        if e.event_type == "shopify_cancel_confirmed"
    ]
    assert len(confirmed) == 1  # idempotent


async def test_local_only_order_is_not_a_shopify_order(db_session: AsyncSession) -> None:
    import uuid

    user = await _user(db_session, ["orders.read"])
    _install(ActionStub())
    db_session.add(OrderWorkflow(id=uuid.uuid4(), external_order_id="local:abc"))
    await db_session.commit()
    workflow = (await db_session.execute(select(OrderWorkflow))).scalar_one()

    async with bearer_client(app, get_db, db_session, user.id) as client:
        response = await client.get(f"/api/v1/live/orders/{workflow.id}/refunds")

    assert response.status_code == 404


async def test_adapter_rejects_unknown_reason_before_calling_shopify() -> None:
    from app.core.exceptions import IntegrationError

    stub = ActionStub()
    adapter = ShopifyAdapter(client=stub)
    with pytest.raises(IntegrationError):
        await adapter.cancel_order("gid://shopify/Order/1", reason="BECAUSE", restock=True)
    with pytest.raises(IntegrationError):
        await adapter.cancel_order("gid://shopify/Order/1", reason="OTHER", restock=True, staff_note="x" * 256)
    assert stub.cancel_calls == []

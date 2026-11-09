"""Live (API-first) abandoned-checkout workflow: pool, assignment (manual +
round-robin), telecaller worklist/detail/calls/follow-ups, conversion
visibility, historical compatibility, Excel export, write prevention.

Shopify is MOCKED. Nothing here is verified against a real store.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from io import BytesIO

import pytest
from app.core.config import settings
from app.db.session import get_db
from app.integrations.registry import clear_adapters, register_adapter
from app.integrations.shopify.adapter import ShopifyAdapter
from app.main import app
from app.models.abandoned_checkout import AbandonedCheckout
from app.models.checkout_workflow import CheckoutWorkflow
from app.models.telecalling import CheckoutAssignment, CheckoutCallAttempt
from app.repositories.telecalling import CheckoutAssignmentRepository
from app.services.order_ref import encode_checkout_ref
from openpyxl import load_workbook
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.telecalling_test_utils import bearer_client, make_abandoned_checkout
from tests.test_live_orders_workflow import _PROVIDER_TABLES
from tests.test_live_telecalling_workflow import _team
from tests.test_shopify_live_mode import _count

pytestmark = pytest.mark.asyncio


class CheckoutStub:
    def __init__(self, nodes: dict[int, dict]) -> None:
        self.nodes = nodes
        self.queries: list[str] = []
        self.more = False

    async def execute(self, query: str, variables: dict | None = None) -> dict:
        assert "abandonedCheckouts" in query
        search = (variables or {}).get("query") or ""
        self.queries.append(search)
        wanted = {int(n) for n in re.findall(r"id:(\d+)", search)}
        picked = [n for k, n in self.nodes.items() if not wanted or k in wanted]
        return {
            "abandonedCheckouts": {
                "pageInfo": {"hasNextPage": self.more, "endCursor": "more" if self.more else None},
                "edges": [{"node": n} for n in picked],
            }
        }


def _node(
    n: int, *, phone: str | None = "9000000000", email: str | None = None, done=False
) -> dict:
    return {
        "id": f"gid://shopify/AbandonedCheckout/{n}",
        "name": f"#C{n}",
        "email": email,
        "phone": phone,
        "abandonedCheckoutUrl": f"https://shop.example/checkouts/{n}",
        "completedAt": "2026-10-05T00:00:00Z" if done else None,
        "createdAt": f"2026-10-{n:02d}T10:00:00Z",
        "updatedAt": f"2026-10-{n:02d}T11:00:00Z",
        "totalPriceSet": {"shopMoney": {"amount": "499.00"}},
        "subtotalPriceSet": {"shopMoney": {"amount": "499.00"}},
        "customer": {"id": "gid://shopify/Customer/1", "firstName": "Asha", "lastName": f"N{n}"},
        "billingAddress": {"name": f"Buyer {n}", "phone": phone},
        "lineItems": {"edges": [{"node": {"title": f"Item {n}", "quantity": 2}}]},
    }


@pytest.fixture(autouse=True)
def _live(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "SHOPIFY_LIVE_MODE", True)
    yield
    clear_adapters()
    app.dependency_overrides.clear()


def _install(nodes: dict[int, dict]) -> CheckoutStub:
    stub = CheckoutStub(nodes)
    register_adapter(ShopifyAdapter(client=stub))
    return stub


def _nodes() -> dict[int, dict]:
    return {
        1: _node(1),
        2: _node(2, phone=None, email="two@example.com"),
        3: _node(3, done=True),  # converted: not an open lead
        4: _node(4, phone=None, email=None),  # anonymous: never a lead
        5: _node(5),
    }


async def _assign(db_session, leader, telecallers, numbers, *, mode="manual"):
    body = {"checkout_ids": [str(encode_checkout_ref(str(n))) for n in numbers], "mode": mode}
    if mode == "manual":
        body["telecaller_id"] = str(telecallers[0].id)
    else:
        body["telecaller_ids"] = [str(t.id) for t in telecallers]
    async with bearer_client(app, get_db, db_session, leader.id) as client:
        return await client.post("/api/v1/team/checkouts/assign", json=body)


async def _assert_no_checkout_snapshots(db_session: AsyncSession) -> None:
    assert await _count(db_session, AbandonedCheckout) == 0
    for model in _PROVIDER_TABLES:
        assert await _count(db_session, model) == 0, model.__name__


async def test_pool_lists_open_contactable_checkouts_only(db_session: AsyncSession) -> None:
    leader, tc1, _ = await _team(db_session)
    _install(_nodes())
    assert (await _assign(db_session, leader, [tc1], [1])).status_code in (200, 201)

    async with bearer_client(app, get_db, db_session, leader.id) as client:
        response = await client.get("/api/v1/team/checkouts")

    assert response.status_code == 200, response.text
    rows = {r["customer_name"]: r for r in response.json()["data"]}
    assert set(rows) == {"Buyer 1", "Buyer 2", "Buyer 5"}  # no completed #3, no anonymous #4
    assert rows["Buyer 1"]["assigned_to_name"] == "Asha"
    assert rows["Buyer 5"]["assigned_to"] is None
    assert rows["Buyer 1"]["checkout_url"].endswith("/1")  # recovery link
    assert rows["Buyer 1"]["item_summary"] == "Item 1"
    await _assert_no_checkout_snapshots(db_session)


async def test_manual_and_round_robin_assignment_store_only_workflow_rows(
    db_session: AsyncSession,
) -> None:
    leader, tc1, tc2 = await _team(db_session)
    _install(_nodes())

    manual = await _assign(db_session, leader, [tc1], [1])
    robin = await _assign(db_session, leader, [tc1, tc2], [2, 5], mode="equal")

    assert manual.status_code in (200, 201) and robin.status_code in (200, 201), robin.text
    rows = (await db_session.execute(select(CheckoutAssignment))).scalars().all()
    assert len(rows) == 3
    per = {}
    for r in rows[1:]:
        per[r.assigned_to] = per.get(r.assigned_to, 0) + 1
    assert sorted(per.values()) == [1, 1]
    workflows = (await db_session.execute(select(CheckoutWorkflow))).scalars().all()
    assert sorted(w.external_checkout_id for w in workflows) == ["1", "2", "5"]
    assert {c.name for c in CheckoutWorkflow.__table__.columns} == {
        "id",
        "source_system",
        "external_checkout_id",
        "notes",
        "converted_at",
        "converted_order_ref",
        "created_at",
        "updated_at",
    }
    await _assert_no_checkout_snapshots(db_session)


async def test_assignment_refuses_recovered_duplicate_and_unknown(db_session: AsyncSession) -> None:
    leader, tc1, _ = await _team(db_session)
    _install(_nodes())

    recovered = await _assign(db_session, leader, [tc1], [3])
    first = await _assign(db_session, leader, [tc1], [1])
    duplicate = await _assign(db_session, leader, [tc1], [1])
    unknown = await _assign(db_session, leader, [tc1], [99])

    assert recovered.status_code == 409
    assert first.status_code in (200, 201)
    assert duplicate.status_code == 409
    assert unknown.status_code == 404
    assert await _count(db_session, CheckoutAssignment) == 1
    assert await _count(db_session, CheckoutWorkflow) == 1  # none for refused ids


async def test_telecaller_worklist_detail_calls_and_follow_up(db_session: AsyncSession) -> None:
    leader, tc1, tc2 = await _team(db_session)
    _install(_nodes())
    await _assign(db_session, leader, [tc1], [1])
    await _assign(db_session, leader, [tc2], [5])
    ref = encode_checkout_ref("1")

    async with bearer_client(app, get_db, db_session, tc1.id) as client:
        worklist = await client.get("/api/v1/telecaller/checkouts")
        detail = await client.get(f"/api/v1/telecaller/checkouts/{ref}")
        call = await client.post(
            f"/api/v1/telecaller/checkouts/{ref}/calls",
            json={"outcome": "interested", "notes": "will pay tomorrow"},
        )
        follow = await client.post(
            f"/api/v1/telecaller/checkouts/{ref}/follow-up",
            json={"next_follow_up_at": "2030-01-01T10:00:00+00:00"},
        )
        history = await client.get(f"/api/v1/telecaller/checkouts/{ref}/calls")
        others = await client.get(f"/api/v1/telecaller/checkouts/{encode_checkout_ref('5')}")

    assert [r["customer_name"] for r in worklist.json()["data"]] == ["Buyer 1"]
    assert detail.status_code == 200 and detail.json()["data"]["customer_phone"] == "9000000000"
    assert call.status_code == 201, call.text
    assert follow.status_code == 200, follow.text
    assert [h["notes"] for h in history.json()["data"]] == ["will pay tomorrow"]
    assert others.status_code in (403, 404)  # another telecaller's lead
    attempt = (await db_session.execute(select(CheckoutCallAttempt))).scalar_one()
    assert attempt.checkout_id == ref
    await _assert_no_checkout_snapshots(db_session)


async def test_conversion_is_read_live_and_a_missing_checkout_is_not_faked(
    db_session: AsyncSession,
) -> None:
    leader, tc1, _ = await _team(db_session)
    stub = _install(_nodes())
    await _assign(db_session, leader, [tc1], [1])
    ref = encode_checkout_ref("1")

    async with bearer_client(app, get_db, db_session, tc1.id) as client:
        before = (await client.get(f"/api/v1/telecaller/checkouts/{ref}")).json()["data"]
        stub.nodes[1] = _node(1, done=True)  # the customer completed checkout in Shopify
        after = (await client.get(f"/api/v1/telecaller/checkouts/{ref}")).json()["data"]
        stub.nodes.pop(1)  # and now Shopify no longer returns it
        gone = await client.get(f"/api/v1/telecaller/checkouts/{ref}")

    assert before["is_recovered"] is False
    assert after["is_recovered"] is True
    assert gone.status_code in (403, 404)


async def test_historical_checkout_keeps_working_in_live_mode(db_session: AsyncSession) -> None:
    leader, tc1, _ = await _team(db_session)
    legacy = await make_abandoned_checkout(db_session, external_id="1")
    await db_session.commit()
    workflow = await db_session.get(CheckoutWorkflow, legacy.id)
    assert workflow is not None  # mirrored when the legacy row was created
    workflow.external_checkout_id = "1"
    await CheckoutAssignmentRepository(db_session).create(
        checkout_id=legacy.id,
        assigned_to=tc1.id,
        assigned_by=leader.id,
        assigned_at=datetime.now(UTC),
        team_leader_id=leader.id,
    )
    await db_session.commit()
    _install(_nodes())

    async with bearer_client(app, get_db, db_session, tc1.id) as client:
        worklist = await client.get("/api/v1/telecaller/checkouts")

    assert [r["checkout_id"] for r in worklist.json()["data"]] == [str(legacy.id)]
    assert worklist.json()["data"][0]["customer_name"] == "Buyer 1"  # live, not the stored row
    assert await db_session.get(AbandonedCheckout, legacy.id) is not None  # history untouched


async def test_excel_export_and_incomplete_flag(db_session: AsyncSession) -> None:
    leader, tc1, _ = await _team(db_session)
    stub = _install(_nodes())
    await _assign(db_session, leader, [tc1], [1])

    async with bearer_client(app, get_db, db_session, leader.id) as client:
        complete = await client.get("/api/v1/team/checkouts/export")
        stub.more = True
        truncated = await client.get("/api/v1/team/checkouts/export")

    sheet = load_workbook(BytesIO(complete.content))["Abandoned checkouts"]
    rows = list(sheet.iter_rows(values_only=True))
    assert rows[0][0] == "Checkout ID" and len(rows) == 4  # header + 3 open leads
    assert any(r[9] == "Asha" and r[10] == "not_called" for r in rows[1:])
    assert "X-Export-Incomplete" not in complete.headers
    assert truncated.headers.get("X-Export-Incomplete") == "true"
    assert load_workbook(BytesIO(truncated.content)).sheetnames[0] == "Export notes"
    await _assert_no_checkout_snapshots(db_session)


async def test_checkout_refs_cannot_be_mistaken_for_order_refs() -> None:
    from app.services.order_ref import (
        decode_checkout_ref,
        decode_shopify_order_ref,
        encode_shopify_order_ref,
    )

    assert decode_checkout_ref(encode_checkout_ref("42")) == "42"
    assert decode_checkout_ref(encode_shopify_order_ref("42")) is None
    assert decode_shopify_order_ref(encode_checkout_ref("42")) is None


async def test_pool_surfaces_an_incomplete_crawl_in_the_response(db_session: AsyncSession) -> None:
    leader, _, _ = await _team(db_session)
    stub = _install(_nodes())
    stub.more = True  # Shopify says there is always another page

    async with bearer_client(app, get_db, db_session, leader.id) as client:
        response = await client.get("/api/v1/team/checkouts")

    meta = response.json()["meta"]
    assert meta["total_is_estimate"] is True
    assert meta["warnings"] and "incomplete" in meta["warnings"][0]

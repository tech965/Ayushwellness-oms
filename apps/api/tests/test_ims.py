"""Inventory Management System (IMS): API + service behaviour on SQLite."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from app.models.ims import ImsLedger
from app.services import ims_catalog, live_catalog
from app.services.ims_service import IMSService
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio

ALL = ["ims.read", "ims.stock.write", "ims.manage"]


def key() -> str:
    return uuid.uuid4().hex


@pytest.fixture(autouse=True)
def _fresh_catalog():
    ims_catalog.clear_cache()
    yield
    ims_catalog.clear_cache()


def _product(pid: str, title: str, ptype: str, variants: list[tuple[str, str | None]], status="active"):
    return {
        "external_id": pid, "title": title, "product_type": ptype, "vendor": "AW",
        "status": status, "image_url": None,
        "variants": [
            {"external_id": vid, "sku": sku if sku else f"shopify-{vid}", "title": "Default Title",
             "barcode": None, "price": "100", "image_url": None}
            for vid, sku in variants
        ],
    }


@pytest.fixture
def shopify(monkeypatch):
    """Two catalogue pages served through the existing live fetcher."""
    pages = [
        ([_product("1", "Gold Sachets", "Herbal Masala", [("11", "GS-1")]),
          _product("2", "Calcium Tablets", "Tablets", [("21", "CAL-1"), ("22", None)])],
         "cur1", True),
        ([_product("3", "Old Thing", "Misc", [("31", "OLD")], status="archived")], None, False),
    ]
    calls: list[str | None] = []

    async def fake_fetch(*, cursor=None, limit=25, query_filter=None):
        calls.append(cursor)
        return pages[1] if cursor == "cur1" else pages[0]

    monkeypatch.setattr(live_catalog, "fetch_products", fake_fetch)
    return calls


async def _setup(db_session, make_authenticated_client):
    mgr = await make_authenticated_client(db_session, permission_codes=ALL, email="mgr@x.com")
    wh = {}
    for code, name in (("251", "Royal Palm"), ("310", "Royal Palm"), ("402", "Office")):
        r = await mgr.post("/api/v1/ims/warehouses", json={"code": code, "name": name})
        assert r.status_code == 201, r.text
        wh[code] = r.json()["data"]["id"]
    return mgr, wh


async def _packaging_item(mgr, name="New Blue Sachets", unit="BOX", units=("BOX", "OUTER", "SACHETS")):
    r = await mgr.post("/api/v1/ims/packaging", json={"name": name, "units": list(units)})
    assert r.status_code == 201, r.text
    return r.json()["data"]["units"][unit]["item_id"], r.json()["data"]


async def _seed_stock(mgr, wh_id, item_id, qty):
    r = await mgr.post(
        "/api/v1/ims/stock/inward",
        json={"idempotency_key": key(), "warehouse_id": wh_id, "item_id": item_id,
              "quantity": qty, "reason": "test receipt"},
    )
    assert r.status_code == 201, r.text
    return r.json()["data"]


async def _qty(mgr, wh_id, item_id) -> int:
    r = await mgr.get(f"/api/v1/ims/items/{item_id}")
    return next(w["quantity"] for w in r.json()["data"]["by_warehouse"] if w["id"] == wh_id)


# ------------------------------------------------------------- warehouses
async def test_warehouse_creation_and_configuration(db_session, make_authenticated_client):
    mgr, wh = await _setup(db_session, make_authenticated_client)
    listed = (await mgr.get("/api/v1/ims/warehouses")).json()["data"]["items"]
    assert [w["code"] for w in listed] == ["251", "310", "402"]

    dup = await mgr.post("/api/v1/ims/warehouses", json={"code": "251", "name": "Again"})
    assert dup.status_code == 409

    r = await mgr.patch(
        f"/api/v1/ims/warehouses/{wh['402']}",
        json={"address": "Office address", "is_active": False},
    )
    assert r.json()["data"]["is_active"] is False
    active = (await mgr.get("/api/v1/ims/warehouses?active_only=true")).json()["data"]["items"]
    assert len(active) == 2


# --------------------------------------------------------------- packaging
async def test_packaging_units_are_independent_items(db_session, make_authenticated_client):
    mgr, wh = await _setup(db_session, make_authenticated_client)
    box, product = await _packaging_item(mgr, "Calcium Tablets", "BOX", ("BOX", "BOTTLE"))
    assert product["tracked_units"] == ["BOX", "BOTTLE"]  # no OUTER/SACHETS/ZIPPER/JAR
    bottle = product["units"]["BOTTLE"]["item_id"]
    await _seed_stock(mgr, wh["251"], box, 3)
    await _seed_stock(mgr, wh["251"], bottle, 480)
    assert await _qty(mgr, wh["251"], box) == 3
    assert await _qty(mgr, wh["251"], bottle) == 480
    # unknown unit is rejected
    bad = await mgr.post("/api/v1/ims/packaging", json={"name": "X", "units": ["PALLET"]})
    assert bad.status_code == 422


async def test_unit_conversions_per_product(db_session, make_authenticated_client):
    mgr, _ = await _setup(db_session, make_authenticated_client)
    _, blue = await _packaging_item(mgr, "New Blue Sachets")
    _, cal = await _packaging_item(mgr, "Calcium Tablets", "BOX", ("BOX", "BOTTLE"))

    r = await mgr.put(
        f"/api/v1/ims/packaging/{blue['id']}/conversions",
        json={"rules": [
            {"larger_unit": "BOX", "smaller_unit": "OUTER", "ratio": 70},
            {"larger_unit": "OUTER", "smaller_unit": "SACHETS", "ratio": 60},
        ]},
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["conversion_summary"] == ["1 BOX = 70 OUTER = 4,200 SACHETS"]
    await mgr.put(
        f"/api/v1/ims/packaging/{cal['id']}/conversions",
        json={"rules": [{"larger_unit": "BOX", "smaller_unit": "BOTTLE", "ratio": 160}]},
    )

    conv = await mgr.post("/api/v1/ims/packaging/convert", json={
        "packaging_product_id": blue["id"], "from_unit": "BOX", "to_unit": "SACHETS", "quantity": 70})
    assert conv.json()["data"]["result"] == 294000
    # ratios are per product, not global
    conv = await mgr.post("/api/v1/ims/packaging/convert", json={
        "packaging_product_id": cal["id"], "from_unit": "BOX", "to_unit": "BOTTLE", "quantity": 3})
    assert conv.json()["data"]["result"] == 480
    frac = await mgr.post("/api/v1/ims/packaging/convert", json={
        "packaging_product_id": blue["id"], "from_unit": "SACHETS", "to_unit": "OUTER", "quantity": 90})
    assert frac.json()["data"]["is_whole_number"] is False
    assert frac.json()["data"]["whole_result"] == 1
    # not configured for this product
    none = await mgr.post("/api/v1/ims/packaging/convert", json={
        "packaging_product_id": cal["id"], "from_unit": "BOX", "to_unit": "JAR", "quantity": 1})
    assert none.status_code == 422


async def test_contradictory_or_invalid_conversions_rejected(db_session, make_authenticated_client):
    mgr, _ = await _setup(db_session, make_authenticated_client)
    _, p = await _packaging_item(mgr)
    url = f"/api/v1/ims/packaging/{p['id']}/conversions"
    contradiction = {"rules": [
        {"larger_unit": "BOX", "smaller_unit": "OUTER", "ratio": 70},
        {"larger_unit": "OUTER", "smaller_unit": "SACHETS", "ratio": 60},
        {"larger_unit": "BOX", "smaller_unit": "SACHETS", "ratio": 4000},
    ]}
    assert (await mgr.put(url, json=contradiction)).status_code == 422
    zero = {"rules": [{"larger_unit": "BOX", "smaller_unit": "OUTER", "ratio": 0}]}
    assert (await mgr.put(url, json=zero)).status_code == 422
    same = {"rules": [{"larger_unit": "BOX", "smaller_unit": "BOX", "ratio": 2}]}
    assert (await mgr.put(url, json=same)).status_code == 422


async def test_conversion_does_not_move_stock(db_session, make_authenticated_client):
    mgr, wh = await _setup(db_session, make_authenticated_client)
    box, p = await _packaging_item(mgr)
    await _seed_stock(mgr, wh["251"], box, 5)
    await mgr.put(f"/api/v1/ims/packaging/{p['id']}/conversions",
                  json={"rules": [{"larger_unit": "BOX", "smaller_unit": "OUTER", "ratio": 70}]})
    await mgr.post("/api/v1/ims/packaging/convert", json={
        "packaging_product_id": p["id"], "from_unit": "BOX", "to_unit": "OUTER", "quantity": 5})
    got = (await mgr.get(f"/api/v1/ims/packaging/{p['id']}")).json()["data"]
    assert got["units"]["BOX"]["quantity"] == 5
    assert got["units"]["OUTER"]["quantity"] == 0  # nothing manufactured


# --------------------------------------------------------------- transfers
async def test_transfer_is_immediate_and_atomic(db_session, make_authenticated_client):
    mgr, wh = await _setup(db_session, make_authenticated_client)
    item, _ = await _packaging_item(mgr)
    await _seed_stock(mgr, wh["251"], item, 100)
    await _seed_stock(mgr, wh["402"], item, 50)

    r = await mgr.post("/api/v1/ims/transfers", json={
        "idempotency_key": key(), "source_warehouse_id": wh["251"],
        "destination_warehouse_id": wh["402"], "lines": [{"item_id": item, "quantity": 20}],
        "notes": "to office"})
    assert r.status_code == 201, r.text
    t = r.json()["data"]
    assert t["reference_no"].startswith("TRF-")
    assert t["created_by"] == "Test User"
    assert await _qty(mgr, wh["251"], item) == 80
    assert await _qty(mgr, wh["402"], item) == 70

    # both ledger legs under the same reference
    moves = (await mgr.get(f"/api/v1/ims/ledger?reference_no={t['reference_no']}")).json()["data"]["items"]
    assert sorted(m["movement_type"] for m in moves) == ["transfer_in", "transfer_out"]
    out = next(m for m in moves if m["movement_type"] == "transfer_out")
    assert (out["quantity_before"], out["quantity_after"], out["quantity_delta"]) == (100, 80, -20)
    detail = (await mgr.get(f"/api/v1/ims/transfers/{t['id']}")).json()["data"]
    assert len(detail["movements"]) == 2


async def test_transfer_rejections(db_session, make_authenticated_client):
    mgr, wh = await _setup(db_session, make_authenticated_client)
    item, _ = await _packaging_item(mgr)
    await _seed_stock(mgr, wh["251"], item, 10)

    def body(src, dst, qty):
        return {"idempotency_key": key(), "source_warehouse_id": wh[src],
                "destination_warehouse_id": wh[dst], "lines": [{"item_id": item, "quantity": qty}]}

    same = await mgr.post("/api/v1/ims/transfers", json=body("251", "251", 1))
    assert same.status_code == 422
    over = await mgr.post("/api/v1/ims/transfers", json=body("251", "402", 11))
    assert over.status_code == 409
    assert over.json()["error"]["details"]["available"] == 10
    assert (await mgr.post("/api/v1/ims/transfers", json=body("251", "402", 0))).status_code == 422
    # nothing moved
    assert await _qty(mgr, wh["251"], item) == 10
    assert await _qty(mgr, wh["402"], item) == 0
    assert (await mgr.get("/api/v1/ims/transfers")).json()["data"]["total_items"] == 0


async def test_transfer_to_inactive_warehouse_rejected(db_session, make_authenticated_client):
    mgr, wh = await _setup(db_session, make_authenticated_client)
    item, _ = await _packaging_item(mgr)
    await _seed_stock(mgr, wh["251"], item, 10)
    await mgr.patch(f"/api/v1/ims/warehouses/{wh['402']}", json={"is_active": False})
    r = await mgr.post("/api/v1/ims/transfers", json={
        "idempotency_key": key(), "source_warehouse_id": wh["251"],
        "destination_warehouse_id": wh["402"], "lines": [{"item_id": item, "quantity": 1}]})
    assert r.status_code == 422


async def test_duplicate_submission_applies_once(db_session, make_authenticated_client):
    mgr, wh = await _setup(db_session, make_authenticated_client)
    item, _ = await _packaging_item(mgr)
    await _seed_stock(mgr, wh["251"], item, 100)
    payload = {"idempotency_key": "same-key-123456", "source_warehouse_id": wh["251"],
               "destination_warehouse_id": wh["402"], "lines": [{"item_id": item, "quantity": 30}]}
    first = await mgr.post("/api/v1/ims/transfers", json=payload)
    second = await mgr.post("/api/v1/ims/transfers", json=payload)
    assert first.status_code == second.status_code == 201
    assert first.json()["data"]["replayed"] is False
    assert second.json()["data"]["replayed"] is True
    assert first.json()["data"]["reference_no"] == second.json()["data"]["reference_no"]
    assert await _qty(mgr, wh["251"], item) == 70
    assert await _qty(mgr, wh["402"], item) == 30


async def test_repeated_transfers_never_overdraw(db_session, make_authenticated_client):
    """Sequential stand-in for a race (real row-lock concurrency is covered by
    the PostgreSQL test): the 4th 30-unit transfer from 100 must fail."""
    mgr, wh = await _setup(db_session, make_authenticated_client)
    item, _ = await _packaging_item(mgr)
    await _seed_stock(mgr, wh["251"], item, 100)
    codes = []
    for _ in range(4):
        r = await mgr.post("/api/v1/ims/transfers", json={
            "idempotency_key": key(), "source_warehouse_id": wh["251"],
            "destination_warehouse_id": wh["310"], "lines": [{"item_id": item, "quantity": 30}]})
        codes.append(r.status_code)
    assert codes == [201, 201, 201, 409]
    assert await _qty(mgr, wh["251"], item) == 10
    assert await _qty(mgr, wh["310"], item) == 90


async def test_transfer_filters(db_session, make_authenticated_client):
    mgr, wh = await _setup(db_session, make_authenticated_client)
    a, _ = await _packaging_item(mgr, "A Product", "BOX", ("BOX",))
    b, _ = await _packaging_item(mgr, "B Product", "BOX", ("BOX",))
    for it in (a, b):
        await _seed_stock(mgr, wh["251"], it, 10)
    for it, dst in ((a, "310"), (b, "402")):
        await mgr.post("/api/v1/ims/transfers", json={
            "idempotency_key": key(), "source_warehouse_id": wh["251"],
            "destination_warehouse_id": wh[dst], "lines": [{"item_id": it, "quantity": 1}]})
    assert (await mgr.get(f"/api/v1/ims/transfers?warehouse_id={wh['402']}")).json()["data"]["total_items"] == 1
    assert (await mgr.get(f"/api/v1/ims/transfers?item_id={a}")).json()["data"]["total_items"] == 1
    assert (await mgr.get("/api/v1/ims/transfers?q=B Product")).json()["data"]["total_items"] == 1
    today = date.today().isoformat()
    assert (await mgr.get(f"/api/v1/ims/transfers?date_from={today}&date_to={today}")).json()["data"]["total_items"] == 2
    assert (await mgr.get("/api/v1/ims/transfers?date_to=2000-01-01")).json()["data"]["total_items"] == 0


# ----------------------------------------------------------------- returns
async def test_external_return_increases_stock_and_party_not_warehouse(db_session, make_authenticated_client):
    mgr, wh = await _setup(db_session, make_authenticated_client)
    box, p = await _packaging_item(mgr)
    party = (await mgr.post("/api/v1/ims/parties", json={"name": "Mithani"})).json()["data"]
    names = [w["name"] for w in (await mgr.get("/api/v1/ims/warehouses")).json()["data"]["items"]]
    assert "Mithani" not in names

    r = await mgr.post("/api/v1/ims/returns", json={
        "idempotency_key": key(), "warehouse_id": wh["402"], "party_id": party["id"],
        "return_date": date.today().isoformat(), "document_no": "DOC-1",
        "lines": [{"item_id": box, "quantity": 5}], "notes": "returned"})
    assert r.status_code == 201, r.text
    data = r.json()["data"]
    assert data["reference_no"].startswith("RET-")
    assert data["party"]["name"] == "Mithani"
    assert await _qty(mgr, wh["402"], box) == 5
    moves = (await mgr.get(f"/api/v1/ims/ledger?reference_no={data['reference_no']}")).json()["data"]["items"]
    assert moves[0]["movement_type"] == "return_in" and moves[0]["actor"] == "Test User"


async def test_return_validation(db_session, make_authenticated_client):
    mgr, wh = await _setup(db_session, make_authenticated_client)
    box, _ = await _packaging_item(mgr)
    party = (await mgr.post("/api/v1/ims/parties", json={"name": "Mithani"})).json()["data"]
    base = {"warehouse_id": wh["402"], "party_id": party["id"],
            "lines": [{"item_id": box, "quantity": 1}]}
    future = (date.today() + timedelta(days=3)).isoformat()
    r = await mgr.post("/api/v1/ims/returns", json={**base, "idempotency_key": key(), "return_date": future})
    assert r.status_code == 422
    r = await mgr.post("/api/v1/ims/returns", json={**base, "idempotency_key": key(),
                                                   "return_date": date.today().isoformat(),
                                                   "party_id": str(uuid.uuid4())})
    assert r.status_code == 404
    # parties are added without code changes; duplicates refused
    assert (await mgr.post("/api/v1/ims/parties", json={"name": "mithani"})).status_code == 409


# -------------------------------------------------------------- adjustments
async def test_adjustments_and_ledger(db_session, make_authenticated_client):
    mgr, wh = await _setup(db_session, make_authenticated_client)
    item, _ = await _packaging_item(mgr)
    await _seed_stock(mgr, wh["251"], item, 100)

    def adj(**kw):
        return mgr.post("/api/v1/ims/stock/adjustments", json={
            "idempotency_key": key(), "warehouse_id": wh["251"], "item_id": item,
            "quantity": 5, "reason": "physical count", **kw})

    assert (await adj(adjustment_type="damaged")).status_code == 201
    assert (await adj(adjustment_type="missing")).status_code == 201
    assert (await adj(adjustment_type="increase")).status_code == 201
    assert (await adj(adjustment_type="count_correction", direction="out")).status_code == 201
    assert await _qty(mgr, wh["251"], item) == 100 - 5 - 5 + 5 - 5

    # direction required for free-form types; conflicts and missing reasons refused
    assert (await adj(adjustment_type="other")).status_code == 422
    assert (await adj(adjustment_type="damaged", direction="in")).status_code == 422
    blank = await mgr.post("/api/v1/ims/stock/adjustments", json={
        "idempotency_key": key(), "warehouse_id": wh["251"], "item_id": item,
        "quantity": 1, "reason": "  ", "adjustment_type": "decrease"})
    assert blank.status_code == 422
    too_much = await adj(adjustment_type="decrease", quantity=10_000)
    assert too_much.status_code == 409

    types = [m["movement_type"] for m in
             (await mgr.get("/api/v1/ims/ledger?page_size=50")).json()["data"]["items"]]
    assert {"damaged", "missing", "adjustment_in", "adjustment_out", "inward"} <= set(types)
    audit = (await mgr.get("/api/v1/ims/stock/adjustments")).json()["data"]
    assert audit["total_items"] == 5 and audit["items"][0]["reason"] == "physical count"


async def test_adjustment_replay_is_idempotent(db_session, make_authenticated_client):
    mgr, wh = await _setup(db_session, make_authenticated_client)
    item, _ = await _packaging_item(mgr)
    await _seed_stock(mgr, wh["251"], item, 10)
    body = {"idempotency_key": "adj-key-123456", "warehouse_id": wh["251"], "item_id": item,
            "quantity": 4, "reason": "broken", "adjustment_type": "damaged"}
    await mgr.post("/api/v1/ims/stock/adjustments", json=body)
    again = await mgr.post("/api/v1/ims/stock/adjustments", json=body)
    assert again.json()["data"]["replayed"] is True
    assert await _qty(mgr, wh["251"], item) == 6


async def test_ledger_is_append_only(db_session, make_authenticated_client):
    mgr, wh = await _setup(db_session, make_authenticated_client)
    item, _ = await _packaging_item(mgr)
    await _seed_stock(mgr, wh["251"], item, 10)
    row = (await db_session.execute(select(ImsLedger))).scalars().first()
    row.quantity_after = 999
    with pytest.raises(ValueError, match="immutable"):
        await db_session.flush()
    await db_session.rollback()
    row = (await db_session.execute(select(ImsLedger))).scalars().first()
    await db_session.delete(row)
    with pytest.raises(ValueError, match="cannot be deleted"):
        await db_session.flush()
    await db_session.rollback()


# ---------------------------------------------------------------- history
async def test_historical_stock_comes_from_ledger_and_opening_balances(db_session, make_authenticated_client):
    mgr, wh = await _setup(db_session, make_authenticated_client)
    item, _ = await _packaging_item(mgr)
    yesterday = date.today() - timedelta(days=1)
    long_ago = date.today() - timedelta(days=10)

    r = await mgr.post("/api/v1/ims/stock/opening-balances", json={
        "as_of": long_ago.isoformat(),
        "lines": [{"warehouse_id": wh["251"], "item_id": item, "quantity": 50}]})
    assert r.status_code == 201, r.text
    await _seed_stock(mgr, wh["251"], item, 25)  # today

    async def as_of(d):
        res = await mgr.get(f"/api/v1/ims/stock/as-of?as_of={d}&warehouse_id={wh['251']}")
        rows = res.json()["data"]["items"]
        return rows[0]["quantity"] if rows else 0

    assert await as_of(long_ago - timedelta(days=1)) == 0
    assert await as_of(long_ago) == 50
    assert await as_of(yesterday) == 50
    assert await as_of(date.today()) == 75

    # opening balance cannot be re-loaded over existing history
    again = await mgr.post("/api/v1/ims/stock/opening-balances", json={
        "as_of": yesterday.isoformat(),
        "lines": [{"warehouse_id": wh["251"], "item_id": item, "quantity": 1}]})
    assert again.status_code == 409
    assert await _qty(mgr, wh["251"], item) == 75


async def test_backdated_return_appears_in_historical_stock(db_session, make_authenticated_client):
    mgr, wh = await _setup(db_session, make_authenticated_client)
    item, _ = await _packaging_item(mgr)
    party = (await mgr.post("/api/v1/ims/parties", json={"name": "Mithani"})).json()["data"]
    d = date.today() - timedelta(days=5)
    await mgr.post("/api/v1/ims/returns", json={
        "idempotency_key": key(), "warehouse_id": wh["402"], "party_id": party["id"],
        "return_date": d.isoformat(), "lines": [{"item_id": item, "quantity": 7}]})
    before = (await mgr.get(f"/api/v1/ims/stock/as-of?as_of={d - timedelta(days=1)}")).json()["data"]["items"]
    on = (await mgr.get(f"/api/v1/ims/stock/as-of?as_of={d}")).json()["data"]["items"]
    assert before == [] and on[0]["quantity"] == 7


async def test_warehouse_wise_stock_and_reports(db_session, make_authenticated_client):
    mgr, wh = await _setup(db_session, make_authenticated_client)
    item, _ = await _packaging_item(mgr)
    await _seed_stock(mgr, wh["251"], item, 100)
    await _seed_stock(mgr, wh["310"], item, 40)
    stock = (await mgr.get("/api/v1/ims/stock")).json()["data"]
    row = next(r for r in stock["items"] if r["id"] == item)
    assert row["by_warehouse"][wh["251"]] == 100 and row["by_warehouse"][wh["310"]] == 40
    assert row["total"] == 140

    rep = (await mgr.get("/api/v1/ims/reports/stock?view=warehouse")).json()["data"]
    assert rep["columns"][0] == "Warehouse" and "as of" in rep["subtitle"]
    assert {r[1] for r in rep["rows"]} == {"251", "310"}
    xlsx = await mgr.get("/api/v1/ims/reports/movements?format=xlsx")
    assert xlsx.status_code == 200 and xlsx.content[:2] == b"PK"
    for name in ("transfers", "returns", "adjustments", "movements"):
        assert (await mgr.get(f"/api/v1/ims/reports/{name}")).status_code == 200
    assert (await mgr.get("/api/v1/ims/reports/nope")).status_code == 422


async def test_dashboard_reflects_real_data(db_session, make_authenticated_client, shopify):
    mgr, wh = await _setup(db_session, make_authenticated_client)
    item, _ = await _packaging_item(mgr)
    await _seed_stock(mgr, wh["251"], item, 100)
    r = await mgr.post("/api/v1/ims/items/finished", json={"product_id": "1", "variant_id": "11"})
    fin = r.json()["data"]["id"]
    await _seed_stock(mgr, wh["310"], fin, 4)  # at/below default threshold -> low stock

    d = (await mgr.get("/api/v1/ims/dashboard")).json()["data"]
    c = d["cards"]
    assert c["total_physical_stock"] == 4  # finished goods only
    assert c["packaging_stock_by_unit"]["BOX"] == 100
    assert c["total_warehouses"] == 3
    assert c["low_stock_items"] == 1
    assert c["out_of_stock_items"] == 2  # OUTER and SACHETS packaging units have no stock
    assert c["recent_stock_movements"] == 2
    assert d["catalog"]["active_products"] == 2 and d["catalog"]["total_skus"] == 3
    assert d["catalog"]["categories"] == 2
    assert {w["code"] for w in d["warehouses"]} == {"251", "310", "402"}
    narrowed = (await mgr.get(f"/api/v1/ims/dashboard?warehouse_id={wh['251']}")).json()["data"]
    assert narrowed["cards"]["total_physical_stock"] == 0


# ------------------------------------------------------------------ shopify
async def test_shopify_catalog_pagination_and_filters(db_session, make_authenticated_client, shopify):
    mgr, _ = await _setup(db_session, make_authenticated_client)
    r = await mgr.get("/api/v1/ims/catalog/products?page_size=10")
    data = r.json()["data"]
    assert shopify == [None, "cur1"]  # followed the cursor to the last page
    assert data["total_items"] == 3
    assert data["stats"] == {"active_products": 2, "total_products": 3, "categories": 2,
                             "total_skus": 3, "missing_sku_variants": 1}

    miss = (await mgr.get("/api/v1/ims/catalog/products?missing_sku=true")).json()["data"]
    assert [p["title"] for p in miss["items"]] == ["Calcium Tablets"]
    assert miss["items"][0]["variants"][1]["sku_missing"] is True
    assert miss["items"][0]["variants"][1]["sku"] is None
    cat = (await mgr.get("/api/v1/ims/catalog/products?category=Herbal+Masala")).json()["data"]
    assert cat["total_items"] == 1
    search = (await mgr.get("/api/v1/ims/catalog/products?q=cal-1")).json()["data"]
    assert search["total_items"] == 1
    cats = (await mgr.get("/api/v1/ims/catalog/categories")).json()["data"]["items"]
    assert {c["category"] for c in cats} == {"Herbal Masala", "Nutraceutical Products"}
    assert len(shopify) == 2  # served from cache after the first load


async def test_register_finished_item_no_duplicates_and_refresh_keeps_stock(
    db_session, make_authenticated_client, shopify, monkeypatch
):
    mgr, wh = await _setup(db_session, make_authenticated_client)
    a = (await mgr.post("/api/v1/ims/items/finished", json={"product_id": "2", "variant_id": "21"})).json()["data"]
    b = (await mgr.post("/api/v1/ims/items/finished", json={"product_id": "2", "variant_id": "21"})).json()["data"]
    assert a["id"] == b["id"] and a["sku"] == "CAL-1"
    wrong = await mgr.post("/api/v1/ims/items/finished", json={"product_id": "1", "variant_id": "21"})
    assert wrong.status_code == 404
    await _seed_stock(mgr, wh["251"], a["id"], 12)

    # Shopify renames the product; a manual refresh updates the label only.
    async def renamed(*, cursor=None, limit=25, query_filter=None):
        return [_product("2", "Calcium Plus", "Tablets", [("21", "CAL-9")])], None, False

    monkeypatch.setattr(live_catalog, "fetch_products", renamed)
    r = await mgr.post("/api/v1/ims/catalog/refresh")
    assert r.json()["data"]["labels_updated"] == 1
    item = (await mgr.get(f"/api/v1/ims/items/{a['id']}")).json()["data"]
    assert item["sku"] == "CAL-9" and "Calcium Plus" in item["name"]
    assert await _qty(mgr, wh["251"], a["id"]) == 12


async def test_shopify_failure_is_reported_gracefully(db_session, make_authenticated_client, monkeypatch):
    from app.core.exceptions import IntegrationError

    async def boom(**_):
        raise IntegrationError("Shopify down")

    monkeypatch.setattr(live_catalog, "fetch_products", boom)
    mgr, _ = await _setup(db_session, make_authenticated_client)
    assert (await mgr.get("/api/v1/ims/catalog/products")).status_code == 502
    d = await mgr.get("/api/v1/ims/dashboard")
    assert d.status_code == 200
    assert d.json()["data"]["catalog"] is None and "Shopify" in d.json()["data"]["warnings"][0]


# -------------------------------------------------------------- permissions
async def test_permissions_enforced_on_backend(db_session, make_authenticated_client):
    mgr, wh = await _setup(db_session, make_authenticated_client)
    item, _ = await _packaging_item(mgr)
    await _seed_stock(mgr, wh["251"], item, 10)

    nobody = await make_authenticated_client(db_session, permission_codes=["orders.read"], email="n@x.com")
    viewer = await make_authenticated_client(db_session, permission_codes=["ims.read"], email="v@x.com")
    staff = await make_authenticated_client(
        db_session, permission_codes=["ims.read", "ims.stock.write"], email="s@x.com")

    assert (await nobody.get("/api/v1/ims/stock")).status_code == 403
    assert (await nobody.get("/api/v1/ims/dashboard")).status_code == 403
    assert (await viewer.get("/api/v1/ims/stock")).status_code == 200

    transfer = {"idempotency_key": key(), "source_warehouse_id": wh["251"],
                "destination_warehouse_id": wh["310"], "lines": [{"item_id": item, "quantity": 1}]}
    assert (await viewer.post("/api/v1/ims/transfers", json=transfer)).status_code == 403
    assert (await staff.post("/api/v1/ims/transfers", json=transfer)).status_code == 201
    # staff cannot configure
    assert (await staff.post("/api/v1/ims/warehouses", json={"code": "9", "name": "X"})).status_code == 403
    assert (await staff.post("/api/v1/ims/packaging", json={"name": "Z"})).status_code == 403
    assert (await staff.post("/api/v1/ims/catalog/refresh")).status_code == 403
    assert (await staff.post("/api/v1/ims/stock/opening-balances", json={
        "as_of": date.today().isoformat(),
        "lines": [{"warehouse_id": wh["251"], "item_id": item, "quantity": 1}]})).status_code == 403
    # unauthenticated
    from httpx import ASGITransport, AsyncClient
    from app.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as anon:
        assert (await anon.get("/api/v1/ims/stock")).status_code == 401


async def test_api_validation_errors(db_session, make_authenticated_client):
    mgr, wh = await _setup(db_session, make_authenticated_client)
    item, _ = await _packaging_item(mgr)
    bad = await mgr.post("/api/v1/ims/transfers", json={
        "idempotency_key": "short", "source_warehouse_id": wh["251"],
        "destination_warehouse_id": wh["310"], "lines": []})
    assert bad.status_code == 422
    assert bad.json()["success"] is False
    missing = await mgr.get(f"/api/v1/ims/items/{uuid.uuid4()}")
    assert missing.status_code == 404
    frac = await mgr.post("/api/v1/ims/stock/inward", json={
        "idempotency_key": key(), "warehouse_id": wh["251"], "item_id": item,
        "quantity": 1.5, "reason": "x y z"})
    assert frac.status_code == 422


async def test_service_level_locking_order_and_ledger_arithmetic(db_session, make_authenticated_client):
    mgr, wh = await _setup(db_session, make_authenticated_client)
    item, _ = await _packaging_item(mgr)
    await _seed_stock(mgr, wh["251"], item, 60)
    await mgr.post("/api/v1/ims/transfers", json={
        "idempotency_key": key(), "source_warehouse_id": wh["251"],
        "destination_warehouse_id": wh["310"], "lines": [{"item_id": item, "quantity": 25}]})
    rows = (await db_session.execute(select(ImsLedger))).scalars().all()
    for r in rows:
        assert r.quantity_after == r.quantity_before + r.quantity_delta >= 0
    # ledger totals equal balances
    svc = IMSService(db_session)
    live = {(r["warehouse"]["code"]): r["quantity"] for r in await svc.stock_as_of(None)}
    assert live == {"251": 35, "310": 25}
    assert datetime.now(UTC) > rows[0].occurred_at


# --------------------------------------------------------- categories page
async def test_categories_overview_product_level_aggregation(db_session, make_authenticated_client, monkeypatch):
    """Categories page: Kits & Combos is no longer returned, and a product
    with several packaging SKUs (Vajrashakti's 1/2/3 Bottles) is reported as
    one row whose `by_warehouse`/`total` sum every one of its SKUs, with no
    double counting."""
    vajrashakti = _product(
        "8471325999293", "Vajrashakti", "Capsules",
        [("v30", "VJR-SKT-30"), ("v60", "VJR-SKT-60"), ("v90", "VJR-SKT-90")],
    )
    kit = _product("8491034673341", "Mardshakti Kit", "Kits", [("kv1", "SHJT-VJR-1")])

    async def fake_fetch(*, cursor=None, limit=25, query_filter=None):
        return ([vajrashakti, kit], None, False)

    monkeypatch.setattr(live_catalog, "fetch_products", fake_fetch)
    mgr, wh = await _setup(db_session, make_authenticated_client)

    async def resolve(variant_id: str) -> str:
        body = {"product_id": "8471325999293", "variant_id": variant_id}
        r = await mgr.post("/api/v1/ims/items/finished", json=body)
        return r.json()["data"]["id"]

    item30 = await resolve("v30")
    item60 = await resolve("v60")
    await _seed_stock(mgr, wh["251"], item30, 10)
    await _seed_stock(mgr, wh["310"], item30, 4)
    await _seed_stock(mgr, wh["251"], item60, 5)

    r = await mgr.get("/api/v1/ims/categories")
    data = r.json()["data"]
    categories = {c["category"] for c in data["items"]}
    assert "Kits & Combos" not in categories

    ayurvedic = next(c for c in data["items"] if c["category"] == "Ayurvedic Products")
    vajra = next(g for g in ayurvedic["groups"] if g["name"] == "Vajrashakti Capsules")
    assert vajra["total"] == 19  # 10 + 4 + 5, across two different SKUs
    assert vajra["by_warehouse"][wh["251"]] == 15
    assert vajra["by_warehouse"][wh["310"]] == 4
    assert vajra["by_warehouse"][wh["402"]] == 0

    options = {o["sku"]: o for o in vajra["packaging_options"]}
    # all 3, even the one never adjusted (not yet resolved into an ImsItem)
    assert set(options) == {"VJR-SKT-30", "VJR-SKT-60", "VJR-SKT-90"}
    assert options["VJR-SKT-30"]["item_id"] == item30
    assert options["VJR-SKT-90"]["item_id"] is None  # never adjusted -> no ImsItem yet
    assert options["VJR-SKT-90"]["product_id"] == "8471325999293"
    assert options["VJR-SKT-90"]["variant_id"] == "v90"


async def test_category_filter_options_survive_shopify_outage(db_session, make_authenticated_client, monkeypatch):
    from app.core.exceptions import IntegrationError

    async def boom(**_):
        raise IntegrationError("Shopify down")

    monkeypatch.setattr(live_catalog, "fetch_products", boom)
    mgr, _ = await _setup(db_session, make_authenticated_client)
    await mgr.post("/api/v1/ims/packaging", json={"name": "Cat Product", "category": "Tablets", "units": ["BOX"]})
    r = await mgr.get("/api/v1/ims/filters/categories")
    assert r.status_code == 200
    assert r.json()["data"] == {"items": ["Tablets"], "shopify_available": False}

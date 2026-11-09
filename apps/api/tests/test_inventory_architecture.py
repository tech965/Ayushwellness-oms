"""OMS-owned inventory architecture: provider variant mappings, reservations,
available-to-promise, dispatch consumption, idempotency, and the live
catalogue joined to OMS stock. Shopify is MOCKED; no catalogue data is stored.
"""

from __future__ import annotations

import re
import uuid

import pytest
from app.core.config import settings
from app.core.exceptions import ConflictError
from app.db.session import get_db
from app.integrations.registry import clear_adapters, register_adapter
from app.integrations.shopify.adapter import ShopifyAdapter
from app.main import app
from app.models.inventory import InventoryMovement
from app.models.inventory_architecture import InventoryReservation, ProviderVariantMapping, Warehouse
from app.models.order_workflow import OrderWorkflow
from app.models.product import Product, ProductVariant
from app.services.inventory_reservation_service import (
    InventoryReservationService,
    VariantMappingService,
    stock_position,
)
from app.services.inventory_service import InventoryService
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from tests.telecalling_test_utils import bearer_client, make_role, make_user

pytestmark = pytest.mark.asyncio


async def _count(session: AsyncSession, model) -> int:
    return (await session.execute(select(func.count()).select_from(model))).scalar_one()


async def _variant(session: AsyncSession, sku: str, *, stock: int, ppb: int = 1, pack: int = 1):
    product = Product(title=f"P-{sku}", status="active")
    session.add(product)
    await session.flush()
    variant = ProductVariant(
        product_id=product.id, sku=sku, title="V", price=10, available_quantity=stock,
        packets_per_box=ppb, pack_size=pack, status="active",
    )
    session.add(variant)
    await session.commit()
    return variant


async def _order(session: AsyncSession, external_id: str) -> OrderWorkflow:
    workflow = OrderWorkflow(external_order_id=external_id)
    session.add(workflow)
    await session.commit()
    return workflow


# --- mappings -----------------------------------------------------------------


async def test_mapping_is_idempotent_and_refuses_conflicts(db_session: AsyncSession) -> None:
    a = await _variant(db_session, "A", stock=5)
    b = await _variant(db_session, "B", stock=5)
    svc = VariantMappingService(db_session)

    first = await svc.map(provider="shopify", external_variant_id="900", variant_id=a.id)
    again = await svc.map(provider="shopify", external_variant_id="900", variant_id=a.id)
    assert first.id == again.id
    with pytest.raises(ConflictError):
        await svc.map(provider="shopify", external_variant_id="900", variant_id=b.id)
    with pytest.raises(ConflictError):
        await svc.map(provider="shopify", external_variant_id="901", variant_id=a.id)
    # A different provider can map the same internal variant: not Shopify-bound.
    other = await svc.map(provider="amazon", external_variant_id="B0-ASIN", variant_id=a.id)
    assert other.provider == "amazon"
    assert await _count(db_session, ProviderVariantMapping) == 2


async def test_database_enforces_mapping_uniqueness(db_session: AsyncSession) -> None:
    a = await _variant(db_session, "A", stock=1)
    db_session.add(ProviderVariantMapping(provider="shopify", external_variant_id="1", variant_id=a.id))
    await db_session.commit()
    db_session.add(ProviderVariantMapping(provider="shopify", external_variant_id="1", variant_id=a.id))
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


async def test_auto_map_by_sku_maps_only_exact_matches(db_session: AsyncSession) -> None:
    a = await _variant(db_session, "SKU-1", stock=1)
    await _variant(db_session, "SKU-2", stock=1)
    catalogue = [
        {"external_variant_id": "11", "external_product_id": "1", "sku": "SKU-1"},
        {"external_variant_id": "12", "external_product_id": "1", "sku": "sku-2"},  # case differs
        {"external_variant_id": "13", "external_product_id": "2", "sku": "NOPE"},
    ]

    result = await VariantMappingService(db_session).auto_map_by_sku("shopify", catalogue)

    assert result["mapped"] == ["11"] and sorted(result["unmatched"]) == ["12", "13"]
    mapping = await VariantMappingService(db_session).lookup("shopify", "11")
    assert mapping.variant_id == a.id


# --- reservations --------------------------------------------------------------------


async def test_reserve_reduces_available_to_promise_not_physical(db_session: AsyncSession) -> None:
    v = await _variant(db_session, "R1", stock=10, ppb=10)
    o = await _order(db_session, "1")

    held = await InventoryReservationService(db_session).reserve(o.id, [(v.id, 30)])  # 3 boxes
    await db_session.commit()

    assert [h.quantity for h in held] == [3]
    pos = await stock_position(db_session, v.id)
    assert (pos.physical, pos.reserved, pos.available_to_promise) == (10, 3, 7)


async def test_reserve_is_all_or_nothing_and_refuses_overselling(db_session: AsyncSession) -> None:
    a = await _variant(db_session, "A1", stock=5)
    b = await _variant(db_session, "B1", stock=1)
    o = await _order(db_session, "2")

    with pytest.raises(ConflictError) as err:
        await InventoryReservationService(db_session).reserve(o.id, [(a.id, 2), (b.id, 2)])
    await db_session.rollback()

    assert err.value.details["items"][0]["sku"] == "B1"
    assert await _count(db_session, InventoryReservation) == 0  # A was not held either


async def test_second_order_cannot_take_stock_already_reserved(db_session: AsyncSession) -> None:
    v = await _variant(db_session, "L1", stock=3)
    first, second = await _order(db_session, "10"), await _order(db_session, "11")
    vid, first_id, second_id = v.id, first.id, second.id  # a rollback expires ORM objects
    svc = InventoryReservationService(db_session)

    await svc.reserve(first_id, [(vid, 2)])
    await db_session.commit()
    with pytest.raises(ConflictError):
        await svc.reserve(second_id, [(vid, 2)])
    await db_session.rollback()
    await svc.reserve(second_id, [(vid, 1)])  # the last box is still available
    await db_session.commit()

    assert (await stock_position(db_session, vid)).available_to_promise == 0


async def test_reserving_twice_for_the_same_order_does_not_double_hold(
    db_session: AsyncSession,
) -> None:
    v = await _variant(db_session, "D1", stock=10)
    o = await _order(db_session, "20")
    svc = InventoryReservationService(db_session)

    await svc.reserve(o.id, [(v.id, 4)])
    await db_session.commit()
    await svc.reserve(o.id, [(v.id, 4)])
    await db_session.commit()

    assert await _count(db_session, InventoryReservation) == 1
    assert (await stock_position(db_session, v.id)).reserved == 4


async def test_release_is_idempotent_and_frees_the_stock(db_session: AsyncSession) -> None:
    v = await _variant(db_session, "F1", stock=4)
    o = await _order(db_session, "30")
    svc = InventoryReservationService(db_session)
    await svc.reserve(o.id, [(v.id, 4)])
    await db_session.commit()

    assert await svc.release(o.id) == 1
    assert await svc.release(o.id) == 0
    await db_session.commit()

    assert (await stock_position(db_session, v.id)).available_to_promise == 4


async def test_dispatch_consumes_reservation_and_replay_never_double_adjusts(
    db_session: AsyncSession,
) -> None:
    from types import SimpleNamespace

    v = await _variant(db_session, "X1", stock=10)
    o = await _order(db_session, "40")
    await InventoryReservationService(db_session).reserve(o.id, [(v.id, 3)])
    await db_session.commit()
    items = [SimpleNamespace(quantity=3, sku="X1", product_variant_id=None, product_name="x")]
    inventory = InventoryService(db_session)

    await inventory.apply_dispatch(order_id=o.id, shipment_id=None, items=items)
    await inventory.apply_dispatch(order_id=o.id, shipment_id=None, items=items)  # replayed event

    await db_session.refresh(v)
    assert v.available_quantity == 7  # deducted exactly once
    movements = (await db_session.execute(select(InventoryMovement))).scalars().all()
    assert len(movements) == 1 and movements[0].warehouse_id is not None
    reservation = (await db_session.execute(select(InventoryReservation))).scalar_one()
    assert reservation.status == "consumed"
    pos = await stock_position(db_session, v.id)
    assert (pos.physical, pos.reserved, pos.available_to_promise) == (7, 0, 7)


async def test_default_warehouse_exists_for_new_movements(db_session: AsyncSession) -> None:
    db_session.add(Warehouse(code="MAIN", name="Main", is_default=True))
    await db_session.commit()
    v = await _variant(db_session, "W1", stock=1)

    movement = await InventoryService(db_session).add_stock(
        v.id, quantity_to_add=5, reason="count", actor=None
    )

    assert movement.warehouse_id is not None
    assert (await db_session.get(ProductVariant, v.id)).available_quantity == 6


# --- live catalogue -----------------------------------------------------------------------


class ProductsStub:
    def __init__(self, products: dict[int, dict]) -> None:
        self.products = products
        self.queries: list[str] = []

    async def execute(self, query: str, variables: dict | None = None) -> dict:
        assert "products(" in query
        search = (variables or {}).get("query") or ""
        self.queries.append(search)
        wanted = {int(n) for n in re.findall(r"id:(\d+)", search)}
        picked = [p for k, p in self.products.items() if not wanted or k in wanted]
        return {
            "products": {
                "pageInfo": {"hasNextPage": False, "endCursor": None},
                "edges": [{"node": p} for p in picked],
            }
        }


def _product(pid: int, title: str, variants: list[tuple[int, str, str]], status="ACTIVE") -> dict:
    return {
        "id": f"gid://shopify/Product/{pid}",
        "title": title,
        "descriptionHtml": "<p>desc</p>",
        "vendor": "AW",
        "productType": "Masala",
        "status": status,
        "tags": [],
        "createdAt": "2026-10-01T00:00:00Z",
        "updatedAt": "2026-10-02T00:00:00Z",
        "featuredImage": {"url": "https://cdn.example/p.png"},
        "variants": {
            "edges": [
                {
                    "node": {
                        "id": f"gid://shopify/ProductVariant/{vid}",
                        "sku": sku,
                        "title": vt,
                        "price": "249.00",
                        "compareAtPrice": None,
                        "inventoryQuantity": 999,
                        "barcode": None,
                        "selectedOptions": [],
                        "image": None,
                    }
                }
                for vid, sku, vt in variants
            ]
        },
    }


@pytest.fixture
def live(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "SHOPIFY_LIVE_MODE", True)
    yield
    clear_adapters()
    app.dependency_overrides.clear()


async def _user(db_session: AsyncSession, perms: list[str]):
    role = await make_role(db_session, name=f"INV-{uuid.uuid4().hex[:5]}", permission_codes=perms)
    return await make_user(db_session, email=f"{uuid.uuid4().hex[:6]}@example.com", role=role)


async def test_live_catalogue_joins_oms_stock_and_stores_no_snapshot(
    db_session: AsyncSession, live
) -> None:
    mapped = await _variant(db_session, "MASALA-50", stock=12)
    await VariantMappingService(db_session).map(
        provider="shopify", external_variant_id="201", external_product_id="20", variant_id=mapped.id
    )
    o = await _order(db_session, "99")
    await InventoryReservationService(db_session).reserve(o.id, [(mapped.id, 2)])
    await db_session.commit()
    before = (await _count(db_session, Product), await _count(db_session, ProductVariant))
    register_adapter(
        ShopifyAdapter(
            client=ProductsStub(
                {20: _product(20, "Herbal Masala", [(201, "MASALA-50", "50g"), (202, "MASALA-100", "100g")])}
            )
        )
    )
    user = await _user(db_session, ["inventory.read"])

    async with bearer_client(app, get_db, db_session, user.id) as client:
        response = await client.get("/api/v1/live/catalog/products")

    assert response.status_code == 200, response.text
    product = response.json()["data"]["items"][0]
    assert product["title"] == "Herbal Masala" and product["image_url"].endswith("p.png")
    rows = {v["sku"]: v for v in product["variants"]}
    assert rows["MASALA-50"]["mapped"] is True
    assert rows["MASALA-50"]["oms"]["physical_quantity"] == 12
    assert rows["MASALA-50"]["oms"]["reserved_quantity"] == 2
    assert rows["MASALA-50"]["oms"]["available_to_promise"] == 10
    assert rows["MASALA-50"]["shopify_inventory_quantity"] == 999  # informational only
    assert rows["MASALA-100"]["mapped"] is False and rows["MASALA-100"]["oms"] is None
    # No Shopify snapshot stored; Shopify's 999 never touched OMS stock.
    assert (await _count(db_session, Product), await _count(db_session, ProductVariant)) == before
    await db_session.refresh(mapped)
    assert mapped.available_quantity == 12


async def test_live_catalogue_filters_and_detail(db_session: AsyncSession, live) -> None:
    stub = ProductsStub(
        {
            20: _product(20, "Herbal Masala", [(201, "MASALA-50", "50g")]),
            21: _product(21, "Mint Candy", [(211, "CANDY-1", "1pc")], status="DRAFT"),
        }
    )
    register_adapter(ShopifyAdapter(client=stub))
    user = await _user(db_session, ["inventory.read"])

    async with bearer_client(app, get_db, db_session, user.id) as client:
        by_sku = await client.get("/api/v1/live/catalog/products", params={"sku": "candy"})
        by_status = await client.get("/api/v1/live/catalog/products", params={"status": "active"})
        by_q = await client.get("/api/v1/live/catalog/products", params={"q": "Masala"})
        detail = await client.get("/api/v1/live/catalog/products/20")
        missing = await client.get("/api/v1/live/catalog/products/999")

    assert [p["title"] for p in by_sku.json()["data"]["items"]] == ["Mint Candy"]
    assert [p["title"] for p in by_status.json()["data"]["items"]] == ["Herbal Masala"]
    assert any('"Masala"' in q for q in stub.queries)  # pushed to Shopify, sanitized/quoted
    assert by_q.status_code == 200 and detail.json()["data"]["title"] == "Herbal Masala"
    assert missing.status_code == 404 or missing.json()["data"] is None


async def test_variant_detail_requires_a_mapping(db_session: AsyncSession, live) -> None:
    v = await _variant(db_session, "MASALA-50", stock=3)
    await VariantMappingService(db_session).map(
        provider="shopify", external_variant_id="201", external_product_id="20", variant_id=v.id
    )
    await db_session.commit()
    register_adapter(
        ShopifyAdapter(client=ProductsStub({20: _product(20, "Herbal Masala", [(201, "MASALA-50", "50g")])}))
    )
    user = await _user(db_session, ["inventory.read"])

    async with bearer_client(app, get_db, db_session, user.id) as client:
        mapped = await client.get("/api/v1/live/catalog/variants/201")
        unmapped = await client.get("/api/v1/live/catalog/variants/777")

    assert mapped.json()["data"]["product_title"] == "Herbal Masala"
    assert mapped.json()["data"]["oms"]["physical_quantity"] == 3
    assert unmapped.status_code == 404


async def test_mapping_endpoints_enforce_permissions_and_audit(
    db_session: AsyncSession, live
) -> None:
    v = await _variant(db_session, "S1", stock=1)
    reader = await _user(db_session, ["inventory.read"])
    manager = await _user(db_session, ["inventory.manage"])
    body = {"external_variant_id": "555", "external_product_id": "5", "variant_id": str(v.id)}

    async with bearer_client(app, get_db, db_session, reader.id) as client:
        denied = await client.post("/api/v1/live/catalog/mappings", json=body)
        denied_auto = await client.post("/api/v1/live/catalog/mappings/auto-map-by-sku")
    async with bearer_client(app, get_db, db_session, manager.id) as client:
        created = await client.post("/api/v1/live/catalog/mappings", json=body)
        repeat = await client.post("/api/v1/live/catalog/mappings", json=body)

    assert denied.status_code == denied_auto.status_code == 403
    assert created.status_code == 201 and repeat.status_code == 201  # idempotent
    assert await _count(db_session, ProviderVariantMapping) == 1


async def test_inventory_overview_shows_oms_stock_and_flags_unavailable_shopify_names(
    db_session: AsyncSession, live
) -> None:
    v = await _variant(db_session, "OV-1", stock=8)
    await VariantMappingService(db_session).map(
        provider="shopify", external_variant_id="301", external_product_id="30", variant_id=v.id
    )
    await db_session.commit()

    class Down:
        async def execute(self, query, variables=None):  # noqa: ANN001
            from app.integrations.shopify.errors import ShopifyApiError

            raise ShopifyApiError("Shopify is down", error_type="network_error")

    register_adapter(ShopifyAdapter(client=Down()))
    user = await _user(db_session, ["inventory.read"])

    async with bearer_client(app, get_db, db_session, user.id) as client:
        response = await client.get("/api/v1/live/catalog/inventory/overview")

    data = response.json()["data"]
    assert data["items"][0]["physical_quantity"] == 8  # OMS stock still shown
    assert data["items"][0]["shopify_title"] is None
    assert data["shopify_names_available"] is False and data["warnings"]  # said, not hidden


async def test_release_reservations_endpoint_is_manage_only_and_idempotent(
    db_session: AsyncSession, live
) -> None:
    v = await _variant(db_session, "RR-1", stock=5)
    o = await _order(db_session, "70")
    await InventoryReservationService(db_session).reserve(o.id, [(v.id, 2)])
    await db_session.commit()
    reader = await _user(db_session, ["inventory.read"])
    manager = await _user(db_session, ["inventory.manage"])
    url = f"/api/v1/live/catalog/inventory/orders/{o.id}/release-reservations"

    async with bearer_client(app, get_db, db_session, reader.id) as client:
        denied = await client.post(url)
    async with bearer_client(app, get_db, db_session, manager.id) as client:
        first = await client.post(url)
        second = await client.post(url)

    assert denied.status_code == 403
    assert first.json()["data"]["released"] == 1 and second.json()["data"]["released"] == 0

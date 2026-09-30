"""Review-meeting requirements 3-5: a telecaller can correct an order
item's product flavour/variant, and every flavour/address change is
recorded on the order's append-only Activity History (`OrderEvent` +
`AuditLog`) with who/when/before/after -- never a silent overwrite.

Reuses the exact existing architecture: `OrderEvent`/`AuditService` (the
same mechanism `update_shipping_address` already writes to), the existing
`ProductVariantRepository.list_for_product`, and the same ownership-check
pattern every other `/telecaller/orders/{id}/*` route uses.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from app.db.session import get_db
from app.main import app
from app.models.order import OrderItem
from app.models.product import Product, ProductVariant
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
    team_leader_role = await make_role(
        db_session, name="TEAM_LEADER", permission_codes=["telecalling.manage"]
    )
    telecaller_role = await make_role(
        db_session, name="TELECALLER", permission_codes=["calls.manage", "orders.confirm"]
    )
    leader = await make_user(db_session, email="leader@flavour.example.com", role=team_leader_role)
    telecaller = await make_user(
        db_session, email="tc@flavour.example.com", role=telecaller_role, team_leader_id=leader.id
    )
    other_telecaller = await make_user(
        db_session, email="tc2@flavour.example.com", role=telecaller_role, team_leader_id=leader.id
    )
    customer = await make_customer(db_session)
    return leader, telecaller, other_telecaller, customer


async def _assign(client, order_id: str, telecaller_id: str) -> None:
    response = await client.post(
        "/api/v1/team/orders/assign",
        json={"order_ids": [order_id], "mode": "manual", "telecaller_id": telecaller_id},
    )
    assert response.status_code == 201


async def _make_product_with_variants(
    db_session: AsyncSession, *, order, base_sku: str, flavours: list[str]
) -> tuple[Product, list[ProductVariant], OrderItem]:
    product = Product(
        source_system="shopify",
        external_id=f"ext-{base_sku}",
        shopify_product_id=f"shopify-{base_sku}",
        title="Ashwagandha Capsules",
    )
    db_session.add(product)
    await db_session.flush()

    variants = []
    for flavour in flavours:
        variant = ProductVariant(
            source_system="shopify",
            external_id=f"ext-{base_sku}-{flavour}",
            shopify_variant_id=f"shopify-{base_sku}-{flavour}",
            product_id=product.id,
            sku=f"{base_sku}-{flavour.upper()}",
            title=flavour,
            price=Decimal("299.00"),
        )
        db_session.add(variant)
        variants.append(variant)
    await db_session.flush()

    item = OrderItem(
        order_id=order.id,
        product_variant_id=variants[0].id,
        sku=variants[0].sku,
        product_name=product.title,
        quantity=1,
        unit_price=Decimal("299.00"),
        total_amount=Decimal("299.00"),
    )
    db_session.add(item)
    await db_session.commit()
    await db_session.refresh(item)
    return product, variants, item


# TEST: available variants for the dropdown -- every other variant of the
# item's own product.
async def test_list_available_variants_returns_the_products_other_flavours(
    db_session: AsyncSession,
) -> None:
    leader, telecaller, _other, customer = await _setup(db_session)
    order = await make_order(db_session, order_number="FLAV-001", customer=customer)
    _product, variants, item = await _make_product_with_variants(
        db_session, order=order, base_sku="ASHWA", flavours=["Orange", "Mango"]
    )
    async with bearer_client(app, get_db, db_session, leader.id) as leader_client:
        await _assign(leader_client, str(order.id), str(telecaller.id))

    async with bearer_client(app, get_db, db_session, telecaller.id) as tc_client:
        response = await tc_client.get(
            f"/api/v1/telecaller/orders/{order.id}/items/{item.id}/variants"
        )
    assert response.status_code == 200
    returned_titles = {v["title"] for v in response.json()["data"]}
    assert returned_titles == {"Orange", "Mango"}


# TEST: changing flavour updates the order item and the response reflects
# the new flavour immediately.
async def test_changing_flavour_updates_the_order_item(db_session: AsyncSession) -> None:
    leader, telecaller, _other, customer = await _setup(db_session)
    order = await make_order(db_session, order_number="FLAV-002", customer=customer)
    _product, variants, item = await _make_product_with_variants(
        db_session, order=order, base_sku="ASHWA", flavours=["Orange", "Mango"]
    )
    async with bearer_client(app, get_db, db_session, leader.id) as leader_client:
        await _assign(leader_client, str(order.id), str(telecaller.id))

    mango = variants[1]
    async with bearer_client(app, get_db, db_session, telecaller.id) as tc_client:
        response = await tc_client.patch(
            f"/api/v1/telecaller/orders/{order.id}/items/{item.id}/variant",
            json={"product_variant_id": str(mango.id)},
        )
        assert response.status_code == 200
        updated_item = response.json()["data"]["items"][0]
        assert updated_item["variant_title"] == "Mango"
        assert updated_item["sku"] == mango.sku
        # Quantity/pricing are deliberately untouched by a flavour change.
        assert updated_item["quantity"] == 1
        assert Decimal(updated_item["unit_price"]) == Decimal("299.00")

        order_view = await tc_client.get(f"/api/v1/telecaller/orders/{order.id}")
    assert order_view.json()["data"]["items"][0]["variant_title"] == "Mango"


# TEST: cannot switch to a variant belonging to a different product.
async def test_cannot_change_to_a_variant_of_a_different_product(db_session: AsyncSession) -> None:
    leader, telecaller, _other, customer = await _setup(db_session)
    order = await make_order(db_session, order_number="FLAV-003", customer=customer)
    _product, _variants, item = await _make_product_with_variants(
        db_session, order=order, base_sku="ASHWA", flavours=["Orange"]
    )
    _other_product, other_variants, _other_item = await _make_product_with_variants(
        db_session,
        order=order,
        base_sku="SHILAJIT",
        flavours=["Original"],
    )
    async with bearer_client(app, get_db, db_session, leader.id) as leader_client:
        await _assign(leader_client, str(order.id), str(telecaller.id))

    async with bearer_client(app, get_db, db_session, telecaller.id) as tc_client:
        response = await tc_client.patch(
            f"/api/v1/telecaller/orders/{order.id}/items/{item.id}/variant",
            json={"product_variant_id": str(other_variants[0].id)},
        )
    assert response.status_code == 409


# TEST: a telecaller cannot change flavour on another telecaller's order.
async def test_telecaller_cannot_change_flavour_on_another_telecallers_order(
    db_session: AsyncSession,
) -> None:
    leader, telecaller, other_telecaller, customer = await _setup(db_session)
    order = await make_order(db_session, order_number="FLAV-004", customer=customer)
    _product, variants, item = await _make_product_with_variants(
        db_session, order=order, base_sku="ASHWA", flavours=["Orange", "Mango"]
    )
    async with bearer_client(app, get_db, db_session, leader.id) as leader_client:
        await _assign(leader_client, str(order.id), str(telecaller.id))

    async with bearer_client(app, get_db, db_session, other_telecaller.id) as other_client:
        response = await other_client.patch(
            f"/api/v1/telecaller/orders/{order.id}/items/{item.id}/variant",
            json={"product_variant_id": str(variants[1].id)},
        )
    assert response.status_code == 403


# TEST: flavour change appears on the order's Activity History with
# before/after values, who changed it, and when.
async def test_flavour_change_is_recorded_on_activity_history(db_session: AsyncSession) -> None:
    leader, telecaller, _other, customer = await _setup(db_session)
    order = await make_order(db_session, order_number="FLAV-005", customer=customer)
    _product, variants, item = await _make_product_with_variants(
        db_session, order=order, base_sku="ASHWA", flavours=["Orange", "Mango"]
    )
    async with bearer_client(app, get_db, db_session, leader.id) as leader_client:
        await _assign(leader_client, str(order.id), str(telecaller.id))

    async with bearer_client(app, get_db, db_session, telecaller.id) as tc_client:
        await tc_client.patch(
            f"/api/v1/telecaller/orders/{order.id}/items/{item.id}/variant",
            json={"product_variant_id": str(variants[1].id)},
        )
        activity = await tc_client.get(f"/api/v1/telecaller/orders/{order.id}/activity")

    assert activity.status_code == 200
    events = activity.json()["data"]
    flavour_events = [e for e in events if e["event_type"] == "item_variant_updated"]
    assert len(flavour_events) == 1
    event = flavour_events[0]
    assert "Orange" in event["description"]
    assert "Mango" in event["description"]
    assert event["actor_user_id"] == str(telecaller.id)
    assert event["created_at"] is not None


# TEST: address change (existing endpoint) also appears on Activity
# History -- proves the same append-only mechanism now has a telecaller-
# visible surface without changing `update_shipping_address` itself.
async def test_address_change_is_recorded_on_activity_history(db_session: AsyncSession) -> None:
    leader, telecaller, _other, customer = await _setup(db_session)
    order = await make_order(db_session, order_number="FLAV-006", customer=customer)
    async with bearer_client(app, get_db, db_session, leader.id) as leader_client:
        await _assign(leader_client, str(order.id), str(telecaller.id))

    address_payload = {
        "contact_name": "Ravi Kumar",
        "contact_phone": "9998887777",
        "line1": "221B New Colony Road",
        "line2": None,
        "city": "Pune",
        "state": "Maharashtra",
        "pin_code": "411001",
        "country": "India",
    }
    async with bearer_client(app, get_db, db_session, telecaller.id) as tc_client:
        patched = await tc_client.patch(
            f"/api/v1/telecaller/orders/{order.id}/address", json=address_payload
        )
        assert patched.status_code == 200
        activity = await tc_client.get(f"/api/v1/telecaller/orders/{order.id}/activity")

    events = activity.json()["data"]
    address_events = [e for e in events if e["event_type"] == "address_updated"]
    assert len(address_events) == 1
    assert address_events[0]["actor_user_id"] == str(telecaller.id)


# TEST: activity history ownership is enforced like every other route.
async def test_activity_history_ownership_is_enforced(db_session: AsyncSession) -> None:
    leader, telecaller, other_telecaller, customer = await _setup(db_session)
    order = await make_order(db_session, order_number="FLAV-007", customer=customer)
    async with bearer_client(app, get_db, db_session, leader.id) as leader_client:
        await _assign(leader_client, str(order.id), str(telecaller.id))

    async with bearer_client(app, get_db, db_session, other_telecaller.id) as other_client:
        response = await other_client.get(f"/api/v1/telecaller/orders/{order.id}/activity")
    assert response.status_code == 403

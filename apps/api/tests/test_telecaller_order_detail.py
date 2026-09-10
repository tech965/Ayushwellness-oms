"""Telecaller order detail page additions from the OMS review meeting:

- Edit Address (`PATCH /telecaller/orders/{id}/address`) + its outbound
  Shopify `orderUpdate` push (`ShopifyFulfillmentService.
  sync_shipping_address`).
- Product line items with image/variant/SKU
  (`AssignedOrderResponse.items`, only populated on the single-order
  detail response — see `to_assigned_order_response`'s `include_items`).
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from app.db.session import get_db
from app.main import app
from app.models.enums import OrderStatus
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
    telecaller_role = await make_role(
        db_session, name="TELECALLER", permission_codes=["calls.manage", "orders.confirm"]
    )
    team_leader_role = await make_role(
        db_session, name="TEAM_LEADER", permission_codes=["telecalling.manage"]
    )
    leader = await make_user(db_session, email="leader@tcdetail.example.com", role=team_leader_role)
    telecaller = await make_user(
        db_session,
        email="tc@tcdetail.example.com",
        role=telecaller_role,
        team_leader_id=leader.id,
    )
    other_telecaller = await make_user(
        db_session,
        email="tc2@tcdetail.example.com",
        role=telecaller_role,
        team_leader_id=leader.id,
    )
    customer = await make_customer(db_session)
    return leader, telecaller, other_telecaller, customer


async def _assign(leader_client, order_id: str, telecaller_id: str) -> None:
    response = await leader_client.post(
        "/api/v1/team/orders/assign",
        json={"order_ids": [order_id], "mode": "manual", "telecaller_id": telecaller_id},
    )
    assert response.status_code == 201


_ADDRESS_PAYLOAD = {
    "contact_name": "Ravi Kumar",
    "contact_phone": "9998887777",
    "line1": "221B New Colony Road",
    "line2": "Near Water Tank",
    "city": "Pune",
    "state": "Maharashtra",
    "pin_code": "411001",
    "country": "India",
}


class _StubShopifyClient:
    def __init__(self, responses: list) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[str, dict | None]] = []

    async def execute(self, query: str, variables: dict | None = None) -> dict:
        self.calls.append((query, variables))
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _order_update_success(order_gid: str = "gid://shopify/Order/900001") -> dict:
    return {
        "orderUpdate": {
            "order": {"id": order_gid, "shippingAddress": {}},
            "userErrors": [],
        }
    }


# --- Edit Address ------------------------------------------------------


async def test_telecaller_can_update_allowed_order_address(db_session: AsyncSession) -> None:
    leader, telecaller, _other, customer = await _setup(db_session)
    order = await make_order(db_session, order_number="TCDETAIL-ADDR-A", customer=customer)
    async with bearer_client(app, get_db, db_session, leader.id) as leader_client:
        await _assign(leader_client, str(order.id), str(telecaller.id))

    async with bearer_client(app, get_db, db_session, telecaller.id) as tc_client:
        response = await tc_client.patch(
            f"/api/v1/telecaller/orders/{order.id}/address", json=_ADDRESS_PAYLOAD
        )
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["shipping_address"]["line1"] == "221B New Colony Road"
        assert data["shipping_address"]["city"] == "Pune"


async def test_oms_address_changes_correctly(db_session: AsyncSession) -> None:
    leader, telecaller, _other, customer = await _setup(db_session)
    order = await make_order(db_session, order_number="TCDETAIL-ADDR-B", customer=customer)
    async with bearer_client(app, get_db, db_session, leader.id) as leader_client:
        await _assign(leader_client, str(order.id), str(telecaller.id))

    async with bearer_client(app, get_db, db_session, telecaller.id) as tc_client:
        response = await tc_client.patch(
            f"/api/v1/telecaller/orders/{order.id}/address", json=_ADDRESS_PAYLOAD
        )
        assert response.status_code == 200

    await db_session.refresh(order)
    assert order.shipping_address["line1"] == "221B New Colony Road"
    assert order.shipping_address["pin_code"] == "411001"
    assert order.shipping_address["contact_phone"] == "9998887777"
    # Untouched by an address edit -- never confused with confirmation/
    # shipping/fulfillment events.
    assert order.status == OrderStatus.CONFIRMED
    assert order.confirmed_by_telecaller_id is None
    assert order.fulfillment_status.value == "unfulfilled"


async def test_shopify_mutation_is_called_with_the_correct_address(
    db_session: AsyncSession,
) -> None:
    from app.integrations.registry import clear_adapters, register_adapter
    from app.integrations.shopify.adapter import ShopifyAdapter

    client = _StubShopifyClient([_order_update_success()])
    register_adapter(ShopifyAdapter(client=client))
    try:
        leader, telecaller, _other, customer = await _setup(db_session)
        order = await make_order(
            db_session,
            order_number="TCDETAIL-ADDR-C",
            customer=customer,
            shopify_order_id="900001",
        )
        async with bearer_client(app, get_db, db_session, leader.id) as leader_client:
            await _assign(leader_client, str(order.id), str(telecaller.id))

        async with bearer_client(app, get_db, db_session, telecaller.id) as tc_client:
            response = await tc_client.patch(
                f"/api/v1/telecaller/orders/{order.id}/address", json=_ADDRESS_PAYLOAD
            )
            assert response.status_code == 200
            assert response.json()["data"]["shipping_address_sync_status"] == "synced"

        assert len(client.calls) == 1
        _query, variables = client.calls[0]
        address_input = variables["input"]["shippingAddress"]
        assert variables["input"]["id"] == "gid://shopify/Order/900001"
        assert address_input["address1"] == "221B New Colony Road"
        assert address_input["address2"] == "Near Water Tank"
        assert address_input["city"] == "Pune"
        assert address_input["province"] == "Maharashtra"
        assert address_input["zip"] == "411001"
        assert address_input["country"] == "India"
        assert address_input["phone"] == "9998887777"
        assert address_input["firstName"] == "Ravi"
        assert address_input["lastName"] == "Kumar"
    finally:
        clear_adapters()


async def test_shopify_address_sync_failure_is_handled_safely(db_session: AsyncSession) -> None:
    """OMS update must never be rolled back or hidden by a Shopify
    failure -- the response still reports the OMS's new address, plus a
    FAILED sync status the caller can act on/retry.
    """
    from app.core.exceptions import IntegrationError
    from app.integrations.registry import clear_adapters, register_adapter
    from app.integrations.shopify.adapter import ShopifyAdapter

    client = _StubShopifyClient([IntegrationError("Shopify is down.", details={})])
    register_adapter(ShopifyAdapter(client=client))
    try:
        leader, telecaller, _other, customer = await _setup(db_session)
        order = await make_order(
            db_session,
            order_number="TCDETAIL-ADDR-D",
            customer=customer,
            shopify_order_id="900004",
        )
        async with bearer_client(app, get_db, db_session, leader.id) as leader_client:
            await _assign(leader_client, str(order.id), str(telecaller.id))

        async with bearer_client(app, get_db, db_session, telecaller.id) as tc_client:
            response = await tc_client.patch(
                f"/api/v1/telecaller/orders/{order.id}/address", json=_ADDRESS_PAYLOAD
            )
            assert response.status_code == 200
            data = response.json()["data"]
            assert data["shipping_address"]["line1"] == "221B New Colony Road"
            assert data["shipping_address_sync_status"] == "failed"
            assert data["shipping_address_sync_error"]

        await db_session.refresh(order)
        assert order.shipping_address["line1"] == "221B New Colony Road"

        # Retry: simply saving again (even unchanged) re-attempts the
        # Shopify push, and must never duplicate/corrupt the OMS side.
        client._responses = [_order_update_success("gid://shopify/Order/900004")]
        async with bearer_client(app, get_db, db_session, telecaller.id) as tc_client:
            retry = await tc_client.patch(
                f"/api/v1/telecaller/orders/{order.id}/address", json=_ADDRESS_PAYLOAD
            )
            assert retry.status_code == 200
            assert retry.json()["data"]["shipping_address_sync_status"] == "synced"
    finally:
        clear_adapters()


async def test_unauthorized_telecaller_cannot_edit_another_telecallers_order(
    db_session: AsyncSession,
) -> None:
    leader, telecaller, other_telecaller, customer = await _setup(db_session)
    order = await make_order(db_session, order_number="TCDETAIL-ADDR-E", customer=customer)
    async with bearer_client(app, get_db, db_session, leader.id) as leader_client:
        await _assign(leader_client, str(order.id), str(telecaller.id))

    async with bearer_client(app, get_db, db_session, other_telecaller.id) as other_client:
        response = await other_client.patch(
            f"/api/v1/telecaller/orders/{order.id}/address", json=_ADDRESS_PAYLOAD
        )
        assert response.status_code == 403

    await db_session.refresh(order)
    assert order.shipping_address is None


async def test_admin_superuser_can_still_edit_any_order_address(db_session: AsyncSession) -> None:
    """Admin access is preserved via the existing superuser bypass, same
    pattern confirm/unconfirm already rely on -- no separate admin-facing
    address-edit endpoint was needed.
    """
    leader, telecaller, _other, customer = await _setup(db_session)
    order = await make_order(db_session, order_number="TCDETAIL-ADDR-F", customer=customer)
    async with bearer_client(app, get_db, db_session, leader.id) as leader_client:
        await _assign(leader_client, str(order.id), str(telecaller.id))

    admin = await make_user(
        db_session, email="admin-addr@tcdetail.example.com", is_superuser=True
    )

    async with bearer_client(app, get_db, db_session, admin.id) as admin_client:
        response = await admin_client.patch(
            f"/api/v1/telecaller/orders/{order.id}/address", json=_ADDRESS_PAYLOAD
        )
        assert response.status_code == 200


async def test_address_edit_validates_required_fields(db_session: AsyncSession) -> None:
    leader, telecaller, _other, customer = await _setup(db_session)
    order = await make_order(db_session, order_number="TCDETAIL-ADDR-G", customer=customer)
    async with bearer_client(app, get_db, db_session, leader.id) as leader_client:
        await _assign(leader_client, str(order.id), str(telecaller.id))

    async with bearer_client(app, get_db, db_session, telecaller.id) as tc_client:
        response = await tc_client.patch(
            f"/api/v1/telecaller/orders/{order.id}/address",
            json={**_ADDRESS_PAYLOAD, "line1": ""},
        )
        assert response.status_code == 422


# --- Product image + name + SKU -----------------------------------------


async def _add_item_with_product(
    db_session: AsyncSession,
    *,
    order,
    sku: str,
    product_name: str,
    variant_title: str | None,
    image_url: str | None,
) -> None:
    product = Product(
        source_system="shopify",
        external_id=f"ext-{sku}",
        shopify_product_id=f"shopify-{sku}",
        title=product_name,
        image_url=image_url,
    )
    db_session.add(product)
    await db_session.flush()
    variant = ProductVariant(
        source_system="shopify",
        external_id=f"ext-variant-{sku}",
        shopify_variant_id=f"shopify-variant-{sku}",
        product_id=product.id,
        sku=sku,
        title=variant_title,
        price=Decimal("199.00"),
    )
    db_session.add(variant)
    await db_session.flush()
    item = OrderItem(
        order_id=order.id,
        product_variant_id=variant.id,
        sku=sku,
        product_name=product_name,
        quantity=2,
        unit_price=Decimal("199.00"),
        total_amount=Decimal("398.00"),
    )
    db_session.add(item)
    await db_session.commit()


async def test_order_detail_shows_image_variant_and_sku_for_line_items(
    db_session: AsyncSession,
) -> None:
    leader, telecaller, _other, customer = await _setup(db_session)
    order = await make_order(db_session, order_number="TCDETAIL-ITEM-A", customer=customer)
    await _add_item_with_product(
        db_session,
        order=order,
        sku="AW-HM-PN-100g",
        product_name="Ayush Wellness Herbal Masala New - Ziplock Big Pouches!",
        variant_title="Pan Masala Flavor / 100 Grams Pouches",
        image_url="https://cdn.shopify.com/example.jpg",
    )
    async with bearer_client(app, get_db, db_session, leader.id) as leader_client:
        await _assign(leader_client, str(order.id), str(telecaller.id))

    async with bearer_client(app, get_db, db_session, telecaller.id) as tc_client:
        response = await tc_client.get(f"/api/v1/telecaller/orders/{order.id}")
        assert response.status_code == 200
        items = response.json()["data"]["items"]
        assert len(items) == 1
        assert items[0]["sku"] == "AW-HM-PN-100g"
        assert items[0]["product_name"] == "Ayush Wellness Herbal Masala New - Ziplock Big Pouches!"
        assert items[0]["variant_title"] == "Pan Masala Flavor / 100 Grams Pouches"
        assert items[0]["image_url"] == "https://cdn.shopify.com/example.jpg"
        assert items[0]["quantity"] == 2
        assert Decimal(items[0]["unit_price"]) == Decimal("199.00")
        assert Decimal(items[0]["total_amount"]) == Decimal("398.00")


async def test_order_detail_handles_missing_image_and_sku_gracefully(
    db_session: AsyncSession,
) -> None:
    """No product/variant resolved (unsynced SKU/manual order) -- `None`
    fields, never a fabricated image URL or invented SKU, and never a 500.
    """
    leader, telecaller, _other, customer = await _setup(db_session)
    order = await make_order(db_session, order_number="TCDETAIL-ITEM-B", customer=customer)
    item = OrderItem(
        order_id=order.id,
        product_variant_id=None,
        sku="",
        product_name="Manually entered item",
        quantity=1,
        unit_price=Decimal("50.00"),
        total_amount=Decimal("50.00"),
    )
    db_session.add(item)
    await db_session.commit()
    async with bearer_client(app, get_db, db_session, leader.id) as leader_client:
        await _assign(leader_client, str(order.id), str(telecaller.id))

    async with bearer_client(app, get_db, db_session, telecaller.id) as tc_client:
        response = await tc_client.get(f"/api/v1/telecaller/orders/{order.id}")
        assert response.status_code == 200
        items = response.json()["data"]["items"]
        assert len(items) == 1
        assert items[0]["sku"] == ""
        assert items[0]["variant_title"] is None
        assert items[0]["image_url"] is None


async def test_order_list_does_not_include_items_payload(db_session: AsyncSession) -> None:
    """`items` is deliberately only populated on the single-order detail
    response, never the paginated list -- see `include_items`.
    """
    leader, telecaller, _other, customer = await _setup(db_session)
    order = await make_order(db_session, order_number="TCDETAIL-ITEM-C", customer=customer)
    await _add_item_with_product(
        db_session,
        order=order,
        sku="SKU-LIST",
        product_name="List Test Product",
        variant_title="Variant",
        image_url="https://cdn.shopify.com/list.jpg",
    )
    async with bearer_client(app, get_db, db_session, leader.id) as leader_client:
        await _assign(leader_client, str(order.id), str(telecaller.id))

    async with bearer_client(app, get_db, db_session, telecaller.id) as tc_client:
        response = await tc_client.get("/api/v1/telecaller/orders")
        assert response.status_code == 200
        row = next(r for r in response.json()["data"] if r["order_id"] == str(order.id))
        assert row["items"] == []

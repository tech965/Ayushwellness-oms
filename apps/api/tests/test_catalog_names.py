"""Manual "Edit Name" for Inventory -- a custom display name
(`products.title_override` / `product_variants.title_override`) that
shadows the Shopify `title` in the Inventory UI.

Covers: the service writes only `title_override` (never `title`, stock,
prices, SKUs, or the movement ledger); the endpoints are gated on
`inventory.manage`; validation rejects blank / oversized names; "Reset to
Shopify Name" clears the override; unrelated rows are untouched; and --
the point of the feature -- a Shopify product resync updates `title` but
leaves `title_override` alone, both via the service upsert and through
the full `SyncService` -> normalizer pipeline.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from app.integrations.registry import clear_adapters, register_adapter
from app.integrations.shopify.adapter import ShopifyAdapter
from app.models.enums import (
    IntegrationStatus,
    IntegrationType,
    SyncType,
)
from app.models.integration import IntegrationCode
from app.repositories.integration import IntegrationRepository
from app.repositories.product import ProductRepository, ProductVariantRepository
from app.schemas.common import PageParams, SortParams
from app.services.inventory_service import InventoryService
from app.services.product_service import ProductService
from app.services.sync_service import SyncService
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio


async def _make_product_with_variant(
    session: AsyncSession,
    *,
    key: str,
    product_title: str = "Shopify Product",
    variant_title: str | None = "Shopify Variant",
    available_quantity: int = 12,
    packets_per_box: int = 6,
    inventory_quantity: int = 3,
    price: str = "199.00",
):
    product, _ = await ProductRepository(session).upsert_by_external_id(
        source_system="shopify", external_id=f"prod-{key}", title=product_title
    )
    variant, _ = await ProductVariantRepository(session).upsert_by_external_id(
        source_system="shopify",
        external_id=f"var-{key}",
        product_id=product.id,
        sku=f"SKU-{key}",
        title=variant_title,
        price=Decimal(price),
        available_quantity=available_quantity,
        packets_per_box=packets_per_box,
        inventory_quantity=inventory_quantity,
    )
    await session.commit()
    return product, variant


def _page() -> PageParams:
    return PageParams(page=1, page_size=50)


def _sort() -> SortParams:
    return SortParams(sort_by=None, sort_order="desc")


# --- service: writes only title_override --------------------------------


async def test_set_product_display_name_sets_only_title_override(db_session: AsyncSession) -> None:
    product, _ = await _make_product_with_variant(db_session, key="P1")

    updated = await InventoryService(db_session).set_product_display_name(
        product.id, name="  Custom Product Name  ", actor=None
    )

    assert updated.title_override == "Custom Product Name"  # trimmed
    assert updated.title == "Shopify Product"  # Shopify value untouched

    refreshed = await ProductRepository(db_session).get_by_id(product.id)
    assert refreshed.title_override == "Custom Product Name"
    assert refreshed.title == "Shopify Product"


async def test_set_variant_display_name_sets_only_title_override_and_moves_no_stock(
    db_session: AsyncSession,
) -> None:
    _, variant = await _make_product_with_variant(
        db_session, key="V1", available_quantity=12, packets_per_box=6
    )
    before = {
        "sku": variant.sku,
        "title": variant.title,
        "available_quantity": variant.available_quantity,
        "inventory_quantity": variant.inventory_quantity,
        "packets_per_box": variant.packets_per_box,
        "price": variant.price,
        "shopify_variant_id": variant.shopify_variant_id,
        "external_id": variant.external_id,
        "product_id": variant.product_id,
    }

    updated = await InventoryService(db_session).set_variant_display_name(
        variant.id, name="Custom Variant Name", actor=None
    )

    assert updated.title_override == "Custom Variant Name"
    refreshed = await ProductVariantRepository(db_session).get_by_id(variant.id)
    assert refreshed.title_override == "Custom Variant Name"
    for field, value in before.items():
        assert getattr(refreshed, field) == value, f"{field} must be unchanged"

    movements, total = await InventoryService(db_session).list_movements(
        page_params=_page(), sort_params=_sort(), product_variant_id=variant.id
    )
    assert total == 0 and movements == []  # a name edit is never a stock movement


async def test_reset_clears_the_override_back_to_shopify_name(db_session: AsyncSession) -> None:
    product, variant = await _make_product_with_variant(db_session, key="R1")
    service = InventoryService(db_session)

    await service.set_product_display_name(product.id, name="Temp", actor=None)
    await service.set_variant_display_name(variant.id, name="Temp", actor=None)

    reset_product = await service.set_product_display_name(product.id, name=None, actor=None)
    reset_variant = await service.set_variant_display_name(variant.id, name=None, actor=None)

    assert reset_product.title_override is None
    assert reset_variant.title_override is None
    assert reset_product.title == "Shopify Product"
    assert reset_variant.title == "Shopify Variant"


async def test_blank_name_via_service_is_treated_as_reset(db_session: AsyncSession) -> None:
    product, _ = await _make_product_with_variant(db_session, key="B1")
    service = InventoryService(db_session)
    await service.set_product_display_name(product.id, name="Something", actor=None)

    result = await service.set_product_display_name(product.id, name="   ", actor=None)
    assert result.title_override is None


async def test_setting_a_name_does_not_touch_other_products(db_session: AsyncSession) -> None:
    target, target_variant = await _make_product_with_variant(db_session, key="T1")
    other, other_variant = await _make_product_with_variant(db_session, key="O1")

    service = InventoryService(db_session)
    await service.set_product_display_name(target.id, name="Renamed", actor=None)
    await service.set_variant_display_name(target_variant.id, name="Renamed V", actor=None)

    refreshed_other = await ProductRepository(db_session).get_by_id(other.id)
    refreshed_other_variant = await ProductVariantRepository(db_session).get_by_id(other_variant.id)
    assert refreshed_other.title_override is None
    assert refreshed_other.title == "Shopify Product"
    assert refreshed_other_variant.title_override is None
    assert refreshed_other_variant.title == "Shopify Variant"


# --- the point of the feature: resync must not overwrite the override ----


async def test_resync_via_service_upsert_preserves_title_override(db_session: AsyncSession) -> None:
    """`ProductService.upsert_synced_product` (the write path every Shopify
    product sync/webhook funnels through) updates `title` but never
    `title_override` -- the normalizer doesn't emit that key.
    """
    await ProductService(db_session).upsert_synced_product(
        source_system="shopify",
        external_id="prod-sync-1",
        title="Original Shopify Title",
        variants=[
            {
                "external_id": "var-sync-1",
                "sku": "SKU-SYNC-1",
                "price": Decimal("50.00"),
                "title": "Original Variant Title",
                "inventory_quantity": 5,
            }
        ],
    )
    product = await ProductRepository(db_session).get_by_source_external_id(
        source_system="shopify", external_id="prod-sync-1"
    )
    variant = await ProductVariantRepository(db_session).get_by_sku("SKU-SYNC-1")

    service = InventoryService(db_session)
    await service.set_product_display_name(product.id, name="My Product Name", actor=None)
    await service.set_variant_display_name(variant.id, name="My Variant Name", actor=None)

    # Shopify changes both titles on a later sync.
    await ProductService(db_session).upsert_synced_product(
        source_system="shopify",
        external_id="prod-sync-1",
        title="CHANGED Shopify Title",
        variants=[
            {
                "external_id": "var-sync-1",
                "sku": "SKU-SYNC-1",
                "price": Decimal("50.00"),
                "title": "CHANGED Variant Title",
                "inventory_quantity": 99,
            }
        ],
    )

    refreshed_product = await ProductRepository(db_session).get_by_id(product.id)
    refreshed_variant = await ProductVariantRepository(db_session).get_by_id(variant.id)

    assert refreshed_product.title == "CHANGED Shopify Title"  # sync updated the Shopify field
    assert refreshed_product.title_override == "My Product Name"  # ... but NOT the custom name
    assert refreshed_variant.title == "CHANGED Variant Title"
    assert refreshed_variant.title_override == "My Variant Name"


def _product_sync_response(*, product_id: str, variant_id: str, sku: str, title: str) -> dict:
    return {
        "products": {
            "pageInfo": {"hasNextPage": False, "endCursor": None},
            "edges": [
                {
                    "node": {
                        "id": f"gid://shopify/Product/{product_id}",
                        "title": title,
                        "vendor": "AyushWellness",
                        "productType": "Supplement",
                        "status": "ACTIVE",
                        "tags": [],
                        "createdAt": "2026-01-01T00:00:00Z",
                        "updatedAt": "2026-01-02T00:00:00Z",
                        "variants": {
                            "edges": [
                                {
                                    "node": {
                                        "id": f"gid://shopify/ProductVariant/{variant_id}",
                                        "sku": sku,
                                        "title": f"{title} / Variant",
                                        "price": "499.00",
                                        "compareAtPrice": None,
                                        "inventoryQuantity": 10,
                                        "weight": None,
                                        "barcode": None,
                                        "selectedOptions": [],
                                    }
                                }
                            ]
                        },
                    }
                }
            ],
        }
    }


class _StubClient:
    def __init__(self, responses: list) -> None:
        self._responses = list(responses)

    async def execute(self, query: str, variables: dict | None = None) -> dict:
        return self._responses.pop(0)


async def test_full_shopify_sync_pipeline_preserves_title_override(
    db_session: AsyncSession,
) -> None:
    """Same guarantee, exercised end-to-end through `SyncService` ->
    `ShopifyProductNormalizer` -> `ProductService`, with a real title
    change between the two sync runs.
    """
    client = _StubClient(
        [
            _product_sync_response(
                product_id="7001", variant_id="8001", sku="SKU-PIPE", title="Pipe Title A"
            ),
            _product_sync_response(
                product_id="7001", variant_id="8001", sku="SKU-PIPE", title="Pipe Title B"
            ),
        ]
    )
    register_adapter(ShopifyAdapter(client=client))
    try:
        integration = await IntegrationRepository(db_session).create(
            name="Shopify",
            code=IntegrationCode.SHOPIFY,
            type=IntegrationType.ECOMMERCE,
            status=IntegrationStatus.DISCONNECTED,
            enabled=True,
        )
        await db_session.commit()
        service = SyncService(db_session)

        await service.run_sync(
            integration_id=integration.id, sync_type=SyncType.FULL, entity_type="products"
        )
        product = await ProductRepository(db_session).get_by_source_external_id(
            source_system="shopify", external_id="7001"
        )
        variant = await ProductVariantRepository(db_session).get_by_sku("SKU-PIPE")
        assert product.title == "Pipe Title A"

        inv = InventoryService(db_session)
        await inv.set_product_display_name(product.id, name="Locked Product Name", actor=None)
        await inv.set_variant_display_name(variant.id, name="Locked Variant Name", actor=None)

        await service.run_sync(
            integration_id=integration.id, sync_type=SyncType.FULL, entity_type="products"
        )

        refreshed_product = await ProductRepository(db_session).get_by_id(product.id)
        refreshed_variant = await ProductVariantRepository(db_session).get_by_id(variant.id)
        assert refreshed_product.title == "Pipe Title B"  # resync moved the Shopify title
        assert refreshed_product.title_override == "Locked Product Name"  # override survived
        assert refreshed_variant.title_override == "Locked Variant Name"
    finally:
        clear_adapters()


# --- HTTP endpoints: permission + validation ----------------------------


async def test_edit_name_endpoints_require_inventory_manage(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    product, variant = await _make_product_with_variant(db_session, key="EP1")

    async with await make_authenticated_client(
        db_session, permission_codes=["inventory.read"], email="readonly@example.com"
    ) as read_client:
        r1 = await read_client.patch(
            f"/api/v1/inventory/products/{product.id}/name", json={"name": "Nope"}
        )
        r2 = await read_client.patch(
            f"/api/v1/inventory/stock/{variant.id}/name", json={"name": "Nope"}
        )
        r3 = await read_client.delete(f"/api/v1/inventory/products/{product.id}/name")
        assert r1.status_code == 403
        assert r2.status_code == 403
        assert r3.status_code == 403

    refreshed = await ProductRepository(db_session).get_by_id(product.id)
    assert refreshed.title_override is None  # the 403s changed nothing


async def test_edit_and_reset_name_endpoints_happy_path(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    product, variant = await _make_product_with_variant(db_session, key="EP2")

    async with await make_authenticated_client(
        db_session, permission_codes=["inventory.read", "inventory.manage"]
    ) as client:
        # product
        resp = await client.patch(
            f"/api/v1/inventory/products/{product.id}/name", json={"name": "  Renamed Product  "}
        )
        assert resp.status_code == 200
        body = resp.json()["data"]
        assert body["title"] == "Shopify Product"
        assert body["title_override"] == "Renamed Product"
        assert body["display_title"] == "Renamed Product"

        # variant
        resp = await client.patch(
            f"/api/v1/inventory/stock/{variant.id}/name", json={"name": "Renamed Variant"}
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["display_title"] == "Renamed Variant"

        # it shows up on the read endpoints
        listing = await client.get("/api/v1/inventory/stock", params={"q": "SKU-EP2"})
        assert listing.json()["data"][0]["display_title"] == "Renamed Product"
        assert listing.json()["data"][0]["title"] == "Shopify Product"

        variants = await client.get(f"/api/v1/inventory/products/{product.id}/variants")
        vbody = variants.json()["data"]
        assert vbody["product_display_title"] == "Renamed Product"
        assert vbody["variants"][0]["display_title"] == "Renamed Variant"
        assert vbody["variants"][0]["variant_title_override"] == "Renamed Variant"

        # reset both
        assert (await client.delete(f"/api/v1/inventory/products/{product.id}/name")).json()[
            "data"
        ]["title_override"] is None
        assert (await client.delete(f"/api/v1/inventory/stock/{variant.id}/name")).json()["data"][
            "display_title"
        ] == "Shopify Variant"

    refreshed_variant = await ProductVariantRepository(db_session).get_by_id(variant.id)
    assert refreshed_variant.title_override is None
    assert refreshed_variant.title == "Shopify Variant"


@pytest.mark.parametrize("bad_name", ["", "   ", "\t\n", "x" * 501])
async def test_edit_product_name_rejects_invalid(
    db_session: AsyncSession, make_authenticated_client, bad_name: str
) -> None:
    product, _ = await _make_product_with_variant(db_session, key=f"BAD{len(bad_name)}")

    async with await make_authenticated_client(
        db_session, permission_codes=["inventory.manage"]
    ) as client:
        resp = await client.patch(
            f"/api/v1/inventory/products/{product.id}/name", json={"name": bad_name}
        )
        assert resp.status_code == 422

    refreshed = await ProductRepository(db_session).get_by_id(product.id)
    assert refreshed.title_override is None


async def test_edit_variant_name_rejects_name_over_255(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    _, variant = await _make_product_with_variant(db_session, key="VLONG")

    async with await make_authenticated_client(
        db_session, permission_codes=["inventory.manage"]
    ) as client:
        resp = await client.patch(
            f"/api/v1/inventory/stock/{variant.id}/name", json={"name": "y" * 256}
        )
        assert resp.status_code == 422


async def test_edit_name_endpoint_404_for_unknown_ids(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    async with await make_authenticated_client(
        db_session, permission_codes=["inventory.manage"]
    ) as client:
        resp = await client.patch(
            "/api/v1/inventory/products/00000000-0000-0000-0000-000000000000/name",
            json={"name": "Ghost"},
        )
        assert resp.status_code == 404


async def test_edit_name_writes_an_audit_log_row(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    from app.repositories.audit_log import AuditLogRepository

    product, _ = await _make_product_with_variant(db_session, key="AUD1")

    async with await make_authenticated_client(
        db_session, permission_codes=["inventory.manage"]
    ) as client:
        await client.patch(
            f"/api/v1/inventory/products/{product.id}/name", json={"name": "Audited Name"}
        )

    logs, total = await AuditLogRepository(db_session).list(
        page_params=_page(), sort_params=_sort()
    )
    entry = next(
        log for log in logs if log.entity_type == "product" and log.entity_id == str(product.id)
    )
    assert entry.action == "inventory.product_name_override_updated"
    assert entry.new_value == {"title_override": "Audited Name"}


async def test_no_op_rename_is_harmless(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    product, _ = await _make_product_with_variant(db_session, key="NOOP1")
    service = InventoryService(db_session)
    await service.set_product_display_name(product.id, name="Same", actor=None)
    # setting the same value again -> returns the row, no error
    again = await service.set_product_display_name(product.id, name="Same", actor=None)
    assert again.title_override == "Same"

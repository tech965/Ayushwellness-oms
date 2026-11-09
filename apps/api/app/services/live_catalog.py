"""Live Shopify catalogue joined to OMS-owned inventory.

Products and variants (titles, SKUs, prices, images, status) are read from
Shopify per request and never stored. They are joined, by
`provider_variant_mappings`, to the OMS-owned stock figures in
`product_variants` / `inventory_reservations`. Shopify's own inventory
quantity is deliberately NOT used as OMS stock and never written to it.

Search syntax pushed to Shopify (`sku:`, `status:`, `id:`) follows the
documented product search fields but has not been run against a real store;
results are re-filtered in memory so a syntax mismatch can only return too
many rows, never wrong ones.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import IntegrationError
from app.integrations.registry import get_adapter
from app.integrations.shopify.adapter import ShopifyAdapter
from app.integrations.shopify.errors import ShopifyApiError
from app.integrations.shopify.live import MAX_PAGE_SIZE
from app.integrations.shopify.queries import PRODUCTS_QUERY
from app.models.inventory_architecture import ProviderVariantMapping
from app.models.integration import IntegrationCode
from app.models.product import ProductVariant
from app.services.inventory_reservation_service import (
    VariantMappingService,
    reserved_quantities,
)

PROVIDER = "shopify"
_STRIP = ("raw_external_payload",)


def _adapter() -> ShopifyAdapter:
    adapter = get_adapter(IntegrationCode.SHOPIFY)
    if not isinstance(adapter, ShopifyAdapter):
        raise IntegrationError(
            "Shopify integration is not configured.", details={"error_type": "not_configured"}
        )
    return adapter


def _clean_term(value: str) -> str:
    return value.replace("\\", " ").replace('"', " ").replace("'", " ").strip()


def build_product_search(
    *,
    q: str | None = None,
    sku: str | None = None,
    status: str | None = None,
    ids: list[str] | None = None,
) -> str | None:
    terms: list[str] = []
    if ids:
        terms.append("(" + " OR ".join(f"id:{i}" for i in ids if i.isdigit()) + ")")
    if q and _clean_term(q):
        terms.append(f'"{_clean_term(q)}"')
    if sku and _clean_term(sku):
        terms.append(f'sku:"{_clean_term(sku)}"')
    if status and status.isalpha():
        terms.append(f"status:{status.lower()}")
    return " ".join(terms) or None


async def fetch_products(
    *, cursor: str | None = None, limit: int = 25, query_filter: str | None = None
) -> tuple[list[dict[str, Any]], str | None, bool]:
    adapter = _adapter()
    client = adapter._get_client()  # noqa: SLF001 - same integration package family
    try:
        data = await client.execute(
            PRODUCTS_QUERY,
            {"first": max(1, min(limit, MAX_PAGE_SIZE)), "after": cursor, "query": query_filter},
        )
    except ShopifyApiError as exc:
        raise IntegrationError(exc.message, details={"error_type": exc.error_type}) from exc
    connection = data.get("products") or {}
    page_info = connection.get("pageInfo") or {}
    items: list[dict[str, Any]] = []
    for edge in connection.get("edges") or []:
        normalized = adapter.normalize("products", edge["node"])
        product = {k: v for k, v in normalized.items() if k not in _STRIP}
        product["variants"] = [
            {k: v for k, v in variant.items() if k not in _STRIP}
            for variant in normalized.get("variants") or []
        ]
        items.append(product)
    has_more = bool(page_info.get("hasNextPage"))
    return items, (page_info.get("endCursor") if has_more else None), has_more


async def fetch_products_by_ids(external_product_ids: list[str]) -> list[dict[str, Any]]:
    ids = [i for i in dict.fromkeys(external_product_ids) if i and i.isdigit()]
    found: list[dict[str, Any]] = []
    for start in range(0, len(ids), MAX_PAGE_SIZE):
        batch = ids[start : start + MAX_PAGE_SIZE]
        items, _, _ = await fetch_products(
            limit=len(batch), query_filter=build_product_search(ids=batch)
        )
        found.extend(items)
    return found


def oms_stock(variant: ProductVariant | None, reserved: int) -> dict[str, Any] | None:
    if variant is None:
        return None
    return {
        "variant_id": str(variant.id),
        "oms_sku": variant.sku,
        "physical_quantity": variant.available_quantity,
        "reserved_quantity": reserved,
        "available_to_promise": variant.available_quantity - reserved,
        "pack_size": variant.pack_size,
        "packets_per_box": variant.packets_per_box,
    }


class LiveCatalogService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.mappings = VariantMappingService(session)

    async def _join_stock(self, products: list[dict[str, Any]]) -> list[dict[str, Any]]:
        external_ids = [
            v["external_id"] for p in products for v in p["variants"] if v.get("external_id")
        ]
        mapping = await self.mappings.mappings_for(PROVIDER, external_ids)
        variant_ids = [m.variant_id for m in mapping.values()]
        variants: dict[uuid.UUID, ProductVariant] = {}
        if variant_ids:
            rows = (
                (
                    await self.session.execute(
                        select(ProductVariant).where(ProductVariant.id.in_(variant_ids))
                    )
                )
                .scalars()
                .all()
            )
            variants = {v.id: v for v in rows}
        reserved = await reserved_quantities(self.session, variant_ids) if variant_ids else {}
        out = []
        for product in products:
            variant_rows = []
            for variant in product["variants"]:
                m = mapping.get(variant.get("external_id") or "")
                oms_variant = variants.get(m.variant_id) if m else None
                variant_rows.append(
                    {
                        "external_variant_id": variant.get("external_id"),
                        "sku": variant.get("sku"),
                        "title": variant.get("title"),
                        "price": variant.get("price"),
                        "compare_at_price": variant.get("compare_at_price"),
                        "barcode": variant.get("barcode"),
                        "image_url": variant.get("image_url"),
                        "mapped": m is not None,
                        # Shopify's own quantity is informational only.
                        "shopify_inventory_quantity": variant.get("inventory_quantity"),
                        "oms": oms_stock(oms_variant, reserved.get(m.variant_id, 0) if m else 0),
                    }
                )
            out.append(
                {
                    "external_product_id": product.get("external_id"),
                    "title": product.get("title"),
                    "description": product.get("description"),
                    "vendor": product.get("vendor"),
                    "product_type": product.get("product_type"),
                    "status": product["status"].value
                    if hasattr(product.get("status"), "value")
                    else product.get("status"),
                    "image_url": product.get("image_url"),
                    "variants": variant_rows,
                }
            )
        return out

    async def list_products(
        self,
        *,
        cursor: str | None,
        limit: int,
        q: str | None,
        sku: str | None,
        status: str | None,
        mapped: bool | None,
    ) -> dict[str, Any]:
        items, next_cursor, has_more = await fetch_products(
            cursor=cursor,
            limit=limit,
            query_filter=build_product_search(q=q, sku=sku, status=status),
        )
        joined = await self._join_stock(items)
        filtered = joined
        if sku:
            needle = sku.lower()
            filtered = [
                p
                for p in filtered
                if any(needle in (v["sku"] or "").lower() for v in p["variants"])
            ]
        if status:
            filtered = [p for p in filtered if (p["status"] or "").lower() == status.lower()]
        if mapped is not None:
            filtered = [p for p in filtered if any(v["mapped"] == mapped for v in p["variants"])]
        return {
            "items": filtered,
            "next_cursor": next_cursor,
            "has_more": has_more,
            # Post-filters can leave a short page even though more exist.
            "page_filtered": len(filtered) != len(joined),
        }

    async def get_product(self, external_product_id: str) -> dict[str, Any] | None:
        found = await fetch_products_by_ids([external_product_id])
        if not found:
            return None
        return (await self._join_stock(found))[0]

    async def get_variant(self, external_variant_id: str) -> dict[str, Any] | None:
        mapping = await self.mappings.lookup(PROVIDER, external_variant_id)
        if mapping is None or not mapping.external_product_id:
            return None
        product = await self.get_product(mapping.external_product_id)
        if product is None:
            return None
        match = next(
            (v for v in product["variants"] if v["external_variant_id"] == external_variant_id),
            None,
        )
        if match is None:
            return None
        return {
            **match,
            "external_product_id": product["external_product_id"],
            "product_title": product["title"],
        }

    async def auto_map_by_sku(self, *, max_pages: int = 20) -> dict[str, Any]:
        catalogue: list[dict[str, Any]] = []
        cursor: str | None = None
        has_more = True
        pages = 0
        while has_more and pages < max_pages:
            items, cursor, has_more = await fetch_products(cursor=cursor, limit=MAX_PAGE_SIZE)
            pages += 1
            for product in items:
                for variant in product["variants"]:
                    if variant.get("external_id"):
                        catalogue.append(
                            {
                                "external_variant_id": variant["external_id"],
                                "external_product_id": product.get("external_id"),
                                "sku": variant.get("sku"),
                            }
                        )
        result = await self.mappings.auto_map_by_sku(PROVIDER, catalogue)
        await self.session.commit()
        return {**result, "complete": not has_more, "variants_scanned": len(catalogue)}

    async def overview(self, *, page: int, page_size: int, q: str | None) -> dict[str, Any]:
        """OMS stock per internal variant (DB only), with live Shopify
        names for the mapped rows on this page."""
        conditions = []
        if q:
            conditions.append(ProductVariant.sku.ilike(f"%{q}%"))
        total = (
            await self.session.execute(
                select(func.count()).select_from(ProductVariant).where(*conditions)
            )
        ).scalar_one()
        rows = (
            (
                await self.session.execute(
                    select(ProductVariant)
                    .where(*conditions)
                    .order_by(ProductVariant.sku)
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            )
            .scalars()
            .all()
        )
        ids = [v.id for v in rows]
        mapping_rows = (
            (
                await self.session.execute(
                    select(ProviderVariantMapping).where(
                        ProviderVariantMapping.provider == PROVIDER,
                        ProviderVariantMapping.variant_id.in_(ids),
                    )
                )
            )
            .scalars()
            .all()
            if ids
            else []
        )
        by_variant = {m.variant_id: m for m in mapping_rows}
        reserved = await reserved_quantities(self.session, ids) if ids else {}

        live_titles: dict[str, str] = {}
        live_error: str | None = None
        product_ids = [m.external_product_id for m in mapping_rows if m.external_product_id]
        if product_ids:
            try:
                for product in await fetch_products_by_ids(product_ids):
                    for variant in product["variants"]:
                        live_titles[variant["external_id"]] = (
                            f"{product['title']} — {variant.get('title') or ''}".strip(" —")
                        )
            except IntegrationError as exc:
                live_error = exc.message  # stock still shown; names flagged unavailable
        items = []
        for variant in rows:
            m = by_variant.get(variant.id)
            items.append(
                {
                    **(oms_stock(variant, reserved.get(variant.id, 0)) or {}),
                    "oms_display_name": variant.title_override,
                    "mapped": m is not None,
                    "external_variant_id": m.external_variant_id if m else None,
                    "shopify_title": live_titles.get(m.external_variant_id) if m else None,
                }
            )
        return {
            "items": items,
            "total_items": total,
            "page": page,
            "page_size": page_size,
            "shopify_names_available": live_error is None,
            "warnings": [f"Shopify names unavailable: {live_error}"] if live_error else [],
        }

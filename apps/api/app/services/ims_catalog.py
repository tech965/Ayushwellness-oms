"""Live Shopify catalogue for the IMS.

Reuses the existing Shopify integration (`live_catalog.fetch_products`, which
goes through the server-side adapter/credentials) and walks every cursor page.
The result is held in a short-lived *in-process* cache (never PostgreSQL):
it makes search / category filters / pagination cheap without creating a
second copy of the catalogue to keep in sync. A manual refresh clears it.

Shopify's own inventory quantity is never read as stock and nothing here
writes to the stock tables except `sync_item_labels`, which only refreshes
descriptive labels (name/SKU/category) of items already registered.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.ims import KIND_FINISHED, ImsItem
from app.services import live_catalog

PAGE_SIZE = 100
MAX_PAGES = 50  # 5,000 products; `truncated` is reported beyond this.
UNCATEGORISED = "Uncategorised"


@dataclass
class CatalogSnapshot:
    products: list[dict[str, Any]]
    fetched_at: float
    truncated: bool = False
    pages: int = 0


@dataclass
class _Cache:
    snapshot: CatalogSnapshot | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


_cache = _Cache()


def clear_cache() -> None:
    _cache.snapshot = None


def _status(product: dict[str, Any]) -> str:
    status = product.get("status")
    return str(getattr(status, "value", status) or "").lower()


def is_placeholder_sku(variant: dict[str, Any]) -> bool:
    """The Shopify normaliser invents `shopify-<id>` when a variant has no SKU."""
    sku = (variant.get("sku") or "").strip()
    return not sku or sku == f"shopify-{variant.get('external_id')}"


def _slim(product: dict[str, Any]) -> dict[str, Any]:
    variants = []
    for v in product.get("variants") or []:
        missing = is_placeholder_sku(v)
        variants.append(
            {
                "variant_id": v.get("external_id"),
                "sku": None if missing else v.get("sku"),
                "sku_missing": missing,
                "title": v.get("title"),
                "barcode": v.get("barcode"),
                "image_url": v.get("image_url") or product.get("image_url"),
                "price": str(v["price"]) if v.get("price") is not None else None,
            }
        )
    return {
        "product_id": product.get("external_id"),
        "title": product.get("title"),
        "category": (product.get("product_type") or "").strip() or UNCATEGORISED,
        "vendor": product.get("vendor"),
        "status": _status(product),
        "image_url": product.get("image_url"),
        "variants": variants,
    }


async def _fetch_all() -> CatalogSnapshot:
    products: list[dict[str, Any]] = []
    cursor: str | None = None
    pages = 0
    has_more = True
    while has_more and pages < MAX_PAGES:
        items, cursor, has_more = await live_catalog.fetch_products(cursor=cursor, limit=PAGE_SIZE)
        pages += 1
        products.extend(_slim(p) for p in items)
        if has_more and not cursor:
            break  # defensive: never loop without a cursor
    return CatalogSnapshot(
        products=products, fetched_at=time.time(), truncated=has_more, pages=pages
    )


async def get_catalog(*, refresh: bool = False) -> CatalogSnapshot:
    ttl = settings.IMS_CATALOG_CACHE_SECONDS
    snap = _cache.snapshot
    if not refresh and snap is not None and time.time() - snap.fetched_at < ttl:
        return snap
    async with _cache.lock:
        snap = _cache.snapshot
        if not refresh and snap is not None and time.time() - snap.fetched_at < ttl:
            return snap
        _cache.snapshot = await _fetch_all()  # raises IntegrationError on Shopify failure
        return _cache.snapshot


def filter_products(
    products: list[dict[str, Any]],
    *,
    q: str | None = None,
    category: str | None = None,
    status: str | None = None,
    missing_sku: bool | None = None,
) -> list[dict[str, Any]]:
    needle = (q or "").strip().lower()
    out = []
    for p in products:
        if category and p["category"] != category:
            continue
        if status and p["status"] != status.lower():
            continue
        if missing_sku is True and not any(v["sku_missing"] for v in p["variants"]):
            continue
        if missing_sku is False and any(v["sku_missing"] for v in p["variants"]):
            continue
        if needle:
            hay = [p["title"] or "", p["category"]] + [
                f"{v['sku'] or ''} {v['title'] or ''} {v['barcode'] or ''}" for v in p["variants"]
            ]
            if not any(needle in h.lower() for h in hay):
                continue
        out.append(p)
    return out


def catalog_stats(products: list[dict[str, Any]]) -> dict[str, Any]:
    active = [p for p in products if p["status"] == "active"]
    return {
        "active_products": len(active),
        "total_products": len(products),
        "categories": len({p["category"] for p in active}),
        "total_skus": sum(len(p["variants"]) for p in active),
        "missing_sku_variants": sum(
            1 for p in products for v in p["variants"] if v["sku_missing"]
        ),
    }


def find_variant(
    products: list[dict[str, Any]], variant_id: str
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    for p in products:
        for v in p["variants"]:
            if v["variant_id"] == variant_id:
                return p, v
    return None


def variant_label(product: dict[str, Any], variant: dict[str, Any]) -> str:
    title = (variant.get("title") or "").strip()
    if not title or title.lower() == "default title":
        return product["title"] or ""
    return f"{product['title']} — {title}"


async def sync_item_labels(session: AsyncSession) -> dict[str, Any]:
    """Refresh the catalogue, then update only the descriptive labels of
    already-registered finished-goods items. Quantities are never touched."""
    snap = await get_catalog(refresh=True)
    index = {v["variant_id"]: (p, v) for p in snap.products for v in p["variants"]}
    items = (
        (await session.execute(select(ImsItem).where(ImsItem.kind == KIND_FINISHED)))
        .scalars()
        .all()
    )
    updated = missing = 0
    for item in items:
        hit = index.get(item.shopify_variant_id or "")
        if hit is None:
            missing += 1
            continue
        product, variant = hit
        label, sku, category = variant_label(product, variant), variant["sku"], product["category"]
        if (item.name, item.sku, item.category) != (label, sku, category):
            item.name, item.sku, item.category = label, sku, category
            updated += 1
    await session.commit()
    return {
        "products": len(snap.products),
        "registered_items": len(items),
        "labels_updated": updated,
        "not_found_in_shopify": missing,
        "truncated": snap.truncated,
        "pages": snap.pages,
    }

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

# IMS-level cat egory buckets (independent of Shopify's `product_type`,
# which doesn't carve the catalogue this way). Herbal Masala already has
# its own Shopify product_type and needs no override. The three products
# below have no Shopify category matching their real-world classification
# as Ayurvedic -- confirmed with the business on 2026-11-17 (Vajrashakti,
# Himalayan Shilajit Drops, Arjuna Plus -- 3 products / 8 SKUs exactly).
# Mardshakti Kit and Gut & Detox Bundle are their own fourth bucket, Kits
# & Combos -- approved 2026-11-17, after first being excluded from the
# other three pending that confirmation. Every other active product falls
# into Nutraceutical Products. Keyed by Shopify product id (stable across
# title/variant edits, unlike title matching).
HERBAL_MASALA = "Herbal Masala"
AYURVEDIC_PRODUCTS = "Ayurvedic Products"
NUTRACEUTICAL_PRODUCTS = "Nutraceutical Products"
KITS_AND_COMBOS = "Kits & Combos"

_AYURVEDIC_PRODUCT_IDS = {
    "8471325999293",  # Vajrashakti
    "8475914043581",  # Arjuna Plus
    "8310135521469",  # Himalayan Shilajit Drops
}

_KITS_AND_COMBOS_PRODUCT_IDS = {
    "8491034673341",  # Mardshakti Kit
    "8210784059581",  # Gut & Detox Bundle
}


def _ims_bucket(product_id: str | None, shopify_product_type: str) -> str:
    if product_id in _AYURVEDIC_PRODUCT_IDS:
        return AYURVEDIC_PRODUCTS
    if product_id in _KITS_AND_COMBOS_PRODUCT_IDS:
        return KITS_AND_COMBOS
    if shopify_product_type == HERBAL_MASALA:
        return HERBAL_MASALA
    return NUTRACEUTICAL_PRODUCTS


# Exact, hand-confirmed hierarchy for the IMS Categories page (Category ->
# group -> SKU). A "group" is a flavour for Herbal Masala (one Shopify
# product, three flavours encoded in its variants) and a product for
# Ayurvedic/Nutraceutical. Packaging text is preserved exactly as supplied
# by the business, not reinterpreted from Shopify's own variant titles
# (e.g. Beauty Gummies' second SKU is "2 Jars + 2 Jars" here even though
# Shopify's variant title says "2 Jars+2 Free" -- same SKU, business's own
# wording). Every SKU below was confirmed live against Shopify on
# 2026-11-17: all 43 exist with these exact codes, nothing invented.
CURATED_CATALOG: dict[str, list[dict[str, Any]]] = {
    HERBAL_MASALA: [
        {
            "name": "Paan Flavour",
            "skus": [("AW-HM-PN-60", "1 Outer"), ("AW-HM-PN-120", "2 Outers"), ("AW-HM-PN-180", "3 Outers")],
        },
        {
            "name": "Gutka Flavour",
            "skus": [("AW-HM-CR-60", "1 Outer"), ("AW-HM-CR-120", "2 Outers"), ("AW-HM-CR-180", "3 Outers")],
        },
        {
            "name": "Royal Tobacco Flavour",
            "skus": [("AW-HM-RG-60", "1 Outer"), ("AW-HM-RG-120", "2 Outers"), ("AW-HM-RG-180", "3 Outers")],
        },
    ],
    AYURVEDIC_PRODUCTS: [
        {
            "name": "Arjuna Plus Tablets",
            "skus": [("ARJ-PLS-30", "1 Bottle"), ("ARJ-PLS-60", "2 Bottles"), ("ARJ-PLS-90", "3 Bottles")],
        },
        {
            "name": "Vajrashakti Capsules",
            "skus": [("VJR-SKT-30", "1 Bottle"), ("VJR-SKT-60", "2 Bottles"), ("VJR-SKT-90", "3 Bottles")],
        },
        {
            "name": "Himalayan Shilajit Drops",
            "skus": [("AW-HS-DP-30", "1 Bottle"), ("AW-HS-DP-60", "2 Bottles")],
        },
    ],
    NUTRACEUTICAL_PRODUCTS: [
        {
            "name": "Sleep Gummies",
            "skus": [("AW-SG-GM-30", "1 Jar"), ("AW-SG-GM-60", "2 Jars"), ("AW-SG-GM-90", "3 Jars")],
        },
        {
            "name": "Beauty Gummies",
            "skus": [("AW-BV-GM-30", "1 Jar"), ("AW-BV-GM-60", "2 Jars + 2 Jars")],
        },
        {
            "name": "Lung Care Tablets",
            "skus": [("AW-LC-TB-30", "1 Bottle"), ("AW-LC-TB-60", "2 Bottles"), ("AW-LC-TB-90", "3 Bottles")],
        },
        {
            "name": "Liver Care Tablets",
            "skus": [("AW-LD-TB-30", "1 Bottle"), ("AW-LD-TB-60", "2 Bottles"), ("AW-LD-TB-90", "3 Bottles")],
        },
        {
            "name": "Immune Care Tablets",
            "skus": [("AW-IC-TB-30", "1 Bottle"), ("AW-IC-TB-60", "2 Bottles"), ("AW-IC-TB-90", "3 Bottles")],
        },
        {
            "name": "Gut Fuel Capsules",
            "skus": [("AW-GF-CP-30", "1 Bottle"), ("AW-GF-CP-60", "2 Bottles"), ("AW-GF-CP-90", "3 Bottles")],
        },
        {
            "name": "Dia Shield Tablets",
            "skus": [("AW-DS-TB-20", "1 Bottle"), ("AW-DS-TB-40", "2 Bottles"), ("AW-DS-TB-60", "3 Bottles")],
        },
        {
            "name": "Calcium + Vitamins",
            "skus": [("AW-CV-TB-30", "1 Bottle"), ("AW-CV-TB-60", "2 Bottles"), ("AW-CV-TB-90", "3 Bottles")],
        },
        {
            "name": "Brain Fuel Capsules",
            "skus": [("AW-BF-CP-30", "1 Bottle"), ("AW-BF-CP-60", "2 Bottles"), ("AW-BF-CP-90", "3 Bottles")],
        },
    ],
    KITS_AND_COMBOS: [
        {"name": "Mardshakti Kit", "skus": [("SHJT-VJR-1", "1 Bottle + 1 Bottle")]},
        {"name": "Gut & Detox Bundle", "skus": [("GTDT-BDL1", "1 Bottle + 1 Bottle")]},
    ],
}

# Hand-confirmed exclusions from the four curated buckets above, reported
# by the Categories page separately rather than guessed into a bucket.
# Empty now that Mardshakti Kit / Gut & Detox Bundle have their own Kits
# & Combos bucket -- kept as a mechanism in case a future product needs
# the same "don't guess, ask" treatment.
UNCLASSIFIED_SKUS: dict[str, str] = {}


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
    product_id = product.get("external_id")
    return {
        "product_id": product_id,
        "title": product.get("title"),
        "category": _ims_bucket(product_id, (product.get("product_type") or "").strip()),
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

"""One-time, explicit, reviewed data operation: populate the OMS image
fields (`products.image_url` / `product_variants.image_url`) for products
that were last synced from Shopify BEFORE those fields were added to the
product query.

WHY THIS IS NEEDED
------------------
The scheduled Shopify sync is INCREMENTAL (`ShopifyAdapter.
fetch_incremental`, filtered by Shopify's own `updated_at`). A product
Shopify's merchant hasn't re-saved since the image feature shipped is
never re-fetched, so its `image_url` stays whatever its ORIGINAL sync
wrote -- NULL for anything created before `featuredImage`/`image` were
added to `PRODUCTS_QUERY`. Webhooks have the same gap (they only fire on
a future edit). This script closes that gap for EVERY existing row in one
full, non-incremental sweep.

SAFE BY DESIGN
--------------
  - DRY-RUN BY DEFAULT. Nothing is written unless `--apply` is passed.
  - Fetches via `ShopifyAdapter.fetch("products", ...)` -- the existing
    integration, existing auth/token layer, existing `PRODUCTS_QUERY`
    (which already selects product `featuredImage.url` and per-variant
    `image.url`). NO `since` filter -> a complete sweep. NO second/custom
    GraphQL query. Pagination follows Shopify's own `pageInfo` until
    there is no next page.
  - Normalises via `adapter.normalize("products", raw)` -- the existing
    `ShopifyProductNormalizer`. Its omission behaviour is preserved: a
    product/variant Shopify has NO image for simply has no `image_url`
    key, which this script reads as "no Shopify image" and NEVER as
    "clear the existing OMS image".
  - Matches OMS rows ONLY by immutable Shopify identifiers
    (`Product.shopify_product_id` / `ProductVariant.shopify_variant_id`).
    Never by UUID, SKU, title, position, or any guess.
  - The ONLY columns it can write are `products.image_url` and
    `product_variants.image_url`, and ONLY on rows that already exist.
    It never creates a Product/ProductVariant, never calls
    `ProductService.upsert_synced_product` (or any catalog upsert), never
    touches `available_quantity` / `inventory_quantity` / `sku` /
    `shopify_*_id` / `title` / `title_override` / `vendor` / `tags` /
    `description` / `status` / `catalog_variant_id` / OrderItems /
    InventoryMovements / Shiprocket / RTO / prices.
  - A Shopify product/variant that does NOT match an existing OMS row is
    a BLOCKING problem (`MISSING OMS PRODUCT` / `MISSING OMS VARIANT`) --
    there is no verified "intentionally not in OMS" exclusion in this
    data model (the sweep and the OMS product sync both ingest every
    product, drafts and archived included), so an unmatched record is
    treated as genuinely missing, never silently skipped and never
    auto-created.
  - An identical image (`oms.image_url == shopify_image_url`) is reported
    `UNCHANGED` and NOT rewritten -> a second `--apply` run is a no-op.
  - `--apply` runs every write in ONE transaction, re-checks for blocking
    problems immediately before committing, and rolls back (no commit,
    non-zero exit) if any exist. Never a partial commit.

Run with:
    python scripts/backfill_shopify_images.py            # dry-run (default)
    python scripts/backfill_shopify_images.py --dry-run   # same, explicit
    python scripts/backfill_shopify_images.py --apply     # REAL WRITE -- only
                                                          # after reviewing the
                                                          # dry-run output above

Run this from a Render Shell session (API or worker service -- both have
DB access + Shopify credentials), exactly like the other one-off scripts
in this directory (see `backfill_catalog_variants.py` /
`shopify_pull_to_local.py`).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass, field
from pathlib import Path

# Run as `python scripts/backfill_shopify_images.py` -- see the matching
# comment in `backfill_catalog_variants.py` for why this path insert is
# required.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import settings  # noqa: E402
from app.core.exceptions import IntegrationError  # noqa: E402
from app.core.logging import configure_logging, get_logger  # noqa: E402
from app.db.session import AsyncSessionLocal, run_with_cleanup  # noqa: E402
from app.integrations.shopify.adapter import ShopifyAdapter  # noqa: E402
from app.integrations.shopify.config import ShopifyConfig  # noqa: E402
from app.models.product import Product, ProductVariant  # noqa: E402
from sqlalchemy import select  # noqa: E402
from sqlalchemy.exc import MultipleResultsFound  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

configure_logging()
logger = get_logger(__name__)

_PAGE_SIZE = 50

# Action classifications for one matched-or-missing record.
ACTION_UNCHANGED = "UNCHANGED"
ACTION_UPDATE = "UPDATE"
ACTION_NO_SHOPIFY_IMAGE = "NO_SHOPIFY_IMAGE"
ACTION_MISSING_OMS_PRODUCT = "MISSING OMS PRODUCT"
ACTION_MISSING_OMS_VARIANT = "MISSING OMS VARIANT"
ACTION_DUPLICATE_OMS_IDENTIFIER = "DUPLICATE OMS IDENTIFIER"

_MATCHED_ACTIONS = frozenset({ACTION_UNCHANGED, ACTION_UPDATE, ACTION_NO_SHOPIFY_IMAGE})


# --- plan data structures (pure; no DB writes happen while building these) -


@dataclass
class RecordPlan:
    kind: str  # "product" | "variant"
    shopify_id: str
    oms_id: object | None  # uuid.UUID of the matched row, or None if unmatched
    current_image_url: str | None
    shopify_image_url: str | None
    action: str
    # For a variant: the Shopify product id it came under -- context only.
    parent_shopify_product_id: str | None = None


@dataclass
class Plan:
    products: list[RecordPlan] = field(default_factory=list)
    variants: list[RecordPlan] = field(default_factory=list)
    shopify_products_fetched: int = 0
    shopify_variants_fetched: int = 0
    api_error: str | None = None

    @staticmethod
    def _count(records: list[RecordPlan], action: str) -> int:
        return sum(1 for r in records if r.action == action)

    @staticmethod
    def _with_action(records: list[RecordPlan], action: str) -> list[RecordPlan]:
        return [r for r in records if r.action == action]

    def matched(self, records: list[RecordPlan]) -> int:
        return sum(1 for r in records if r.action in _MATCHED_ACTIONS)

    @property
    def missing_products(self) -> list[RecordPlan]:
        return self._with_action(self.products, ACTION_MISSING_OMS_PRODUCT)

    @property
    def missing_variants(self) -> list[RecordPlan]:
        return self._with_action(self.variants, ACTION_MISSING_OMS_VARIANT)

    @property
    def duplicate_products(self) -> list[RecordPlan]:
        return self._with_action(self.products, ACTION_DUPLICATE_OMS_IDENTIFIER)

    @property
    def duplicate_variants(self) -> list[RecordPlan]:
        return self._with_action(self.variants, ACTION_DUPLICATE_OMS_IDENTIFIER)

    @property
    def product_invariant_ok(self) -> bool:
        return self.shopify_products_fetched == (
            self.matched(self.products) + len(self.missing_products) + len(self.duplicate_products)
        )

    @property
    def variant_invariant_ok(self) -> bool:
        return self.shopify_variants_fetched == (
            self.matched(self.variants) + len(self.missing_variants) + len(self.duplicate_variants)
        )


def _redacted_db_target() -> str:
    """`postgresql+asyncpg://user:pw@host:5432/db` -> `postgresql+asyncpg://
    ***@host:5432/db`. Only host/port/database/drivername are read -- the
    username and password are never touched, so nothing secret can leak.
    """
    try:
        from sqlalchemy.engine import make_url

        url = make_url(settings.DATABASE_URL)
        host = url.host or "(local)"
        port = f":{url.port}" if url.port else ""
        database = url.database or "(none)"
        return f"{url.drivername}://***@{host}{port}/{database}"
    except Exception:  # noqa: BLE001 -- never crash the run over a display string
        return "(unparseable DATABASE_URL -- redacted)"


def _classify(current_image: str | None, shopify_image: str | None) -> str:
    if not shopify_image:
        # Shopify has no image for this record -> leave the OMS value
        # exactly as it is (it may already hold a valid image).
        return ACTION_NO_SHOPIFY_IMAGE
    if current_image == shopify_image:
        return ACTION_UNCHANGED
    return ACTION_UPDATE


async def _lookup_one(session: AsyncSession, model, column, value):  # noqa: ANN001
    """Single-row lookup by an immutable Shopify identifier. Returns
    `(row_or_None, is_duplicate)`. `is_duplicate` is True only if the OMS
    somehow holds >1 row for this identifier (both columns are
    `unique=True`, so this is a corruption guard, not an expected path).
    """
    try:
        row = (await session.execute(select(model).where(column == value))).scalar_one_or_none()
        return row, False
    except MultipleResultsFound:
        return None, True


async def _iter_normalized_products(adapter: ShopifyAdapter):
    """Full, NON-incremental paginated sweep of `PRODUCTS_QUERY` via the
    existing adapter. Yields the normalised product dict for every Shopify
    product, following `pageInfo` to the end.
    """
    cursor: str | None = None
    while True:
        page = await adapter.fetch("products", cursor=cursor, limit=_PAGE_SIZE)
        for raw in page.nodes:
            yield adapter.normalize("products", raw)
        if not page.has_more or page.next_cursor is None:
            break
        cursor = page.next_cursor


def _plan_product(
    session_product, spid: str, shopify_image: str | None, is_duplicate: bool
) -> RecordPlan:  # noqa: ANN001
    if is_duplicate:
        return RecordPlan(
            "product", spid, None, None, shopify_image, ACTION_DUPLICATE_OMS_IDENTIFIER
        )
    if session_product is None:
        return RecordPlan("product", spid, None, None, shopify_image, ACTION_MISSING_OMS_PRODUCT)
    return RecordPlan(
        "product",
        spid,
        session_product.id,
        session_product.image_url,
        shopify_image,
        _classify(session_product.image_url, shopify_image),
    )


def _plan_variant(
    session_variant, svid: str, parent_spid: str, shopify_image: str | None, is_duplicate: bool
) -> RecordPlan:  # noqa: ANN001
    if is_duplicate:
        return RecordPlan(
            "variant",
            svid,
            None,
            None,
            shopify_image,
            ACTION_DUPLICATE_OMS_IDENTIFIER,
            parent_shopify_product_id=parent_spid,
        )
    if session_variant is None:
        return RecordPlan(
            "variant",
            svid,
            None,
            None,
            shopify_image,
            ACTION_MISSING_OMS_VARIANT,
            parent_shopify_product_id=parent_spid,
        )
    return RecordPlan(
        "variant",
        svid,
        session_variant.id,
        session_variant.image_url,
        shopify_image,
        _classify(session_variant.image_url, shopify_image),
        parent_shopify_product_id=parent_spid,
    )


async def build_plan(session: AsyncSession, adapter: ShopifyAdapter) -> Plan:
    """Pure, read-only. Sweeps every Shopify product, matches it (and each
    of its variants) to an existing OMS row by immutable Shopify id, and
    classifies the image action. Issues only SELECTs -- never writes.
    """
    plan = Plan()

    try:
        async for norm in _iter_normalized_products(adapter):
            plan.shopify_products_fetched += 1
            spid = norm.get("shopify_product_id") or ""
            # `.get()` returns None when the normaliser OMITTED the key
            # (Shopify has no image) -- it never emits `image_url: None`.
            shopify_product_image = norm.get("image_url")

            db_product, dup = await _lookup_one(session, Product, Product.shopify_product_id, spid)
            plan.products.append(_plan_product(db_product, spid, shopify_product_image, dup))

            for v in norm.get("variants") or []:
                plan.shopify_variants_fetched += 1
                svid = v.get("shopify_variant_id") or ""
                shopify_variant_image = v.get("image_url")

                db_variant, dup_v = await _lookup_one(
                    session, ProductVariant, ProductVariant.shopify_variant_id, svid
                )
                plan.variants.append(
                    _plan_variant(db_variant, svid, spid, shopify_variant_image, dup_v)
                )
    except IntegrationError as exc:
        plan.api_error = f"{type(exc).__name__}: {exc.message}"
    except Exception as exc:  # noqa: BLE001 -- any fetch failure blocks, never partially proceeds
        plan.api_error = f"{type(exc).__name__}: {exc}"

    return plan


def has_blocking_problems(plan: Plan) -> bool:
    return bool(
        plan.api_error
        or plan.missing_products
        or plan.missing_variants
        or plan.duplicate_products
        or plan.duplicate_variants
        or not plan.product_invariant_ok
        or not plan.variant_invariant_ok
    )


# --- reporting --------------------------------------------------------


def print_report(plan: Plan, *, apply: bool) -> None:
    mode = "APPLY (pre-write check)" if apply else "DRY RUN"
    print("=" * 60)
    print(f"SHOPIFY IMAGE BACKFILL — {mode}")
    print("=" * 60)

    p_update = Plan._count(plan.products, ACTION_UPDATE)
    p_unchanged = Plan._count(plan.products, ACTION_UNCHANGED)
    p_no_image = Plan._count(plan.products, ACTION_NO_SHOPIFY_IMAGE)
    v_update = Plan._count(plan.variants, ACTION_UPDATE)
    v_unchanged = Plan._count(plan.variants, ACTION_UNCHANGED)
    v_no_image = Plan._count(plan.variants, ACTION_NO_SHOPIFY_IMAGE)

    print(f"\nSHOPIFY PRODUCTS FETCHED: {plan.shopify_products_fetched}")
    print(f"OMS PRODUCTS MATCHED: {plan.matched(plan.products)}")
    print(f"OMS PRODUCTS UPDATED: {p_update}")
    print(f"OMS PRODUCTS UNCHANGED: {p_unchanged}")
    print(f"PRODUCTS WITH NO SHOPIFY IMAGE: {p_no_image}")

    print(f"\nSHOPIFY VARIANTS FETCHED: {plan.shopify_variants_fetched}")
    print(f"OMS VARIANTS MATCHED: {plan.matched(plan.variants)}")
    print(f"OMS VARIANTS UPDATED: {v_update}")
    print(f"OMS VARIANTS UNCHANGED: {v_unchanged}")
    print(f"VARIANTS WITH NO SHOPIFY IMAGE: {v_no_image}")

    print(f"\nMISSING OMS PRODUCTS: {len(plan.missing_products)}")
    for r in plan.missing_products:
        print(f"  - MISSING OMS PRODUCT: shopify_product_id={r.shopify_id}")
    print(f"MISSING OMS VARIANTS: {len(plan.missing_variants)}")
    for r in plan.missing_variants:
        print(
            f"  - MISSING OMS VARIANT: shopify_variant_id={r.shopify_id} "
            f"(under shopify_product_id={r.parent_shopify_product_id})"
        )
    dupes = plan.duplicate_products + plan.duplicate_variants
    print(f"DUPLICATE SHOPIFY IDENTIFIERS: {len(dupes)}")
    for r in dupes:
        print(f"  - DUPLICATE OMS {r.kind.upper()} identifier: {r.shopify_id}")

    api_errors = 1 if plan.api_error else 0
    print(f"API ERRORS: {api_errors}")
    if plan.api_error:
        print(f"  - {plan.api_error}")

    blocking = len(plan.missing_products) + len(plan.missing_variants) + len(dupes) + api_errors
    print(f"\nBLOCKING PROBLEMS: {blocking}")

    updates = [r for r in (plan.products + plan.variants) if r.action == ACTION_UPDATE]
    if updates:
        print("\n" + "-" * 60)
        print("RECORDS THAT WOULD BE UPDATED (old -> new)")
        print("-" * 60)
        for r in updates:
            id_label = (
                f"shopify_product_id={r.shopify_id}"
                if r.kind == "product"
                else f"shopify_variant_id={r.shopify_id}"
            )
            print(f"  [{r.kind}] {id_label}")
            print(f"      old: {r.current_image_url!r}")
            print(f"      new: {r.shopify_image_url!r}")

    print("\n" + "=" * 60)
    print("FINAL INVARIANT")
    print("=" * 60)
    print(
        "Shopify products fetched = matched + missing + dup:  "
        f"{'PASS' if plan.product_invariant_ok else 'FAIL'}"
    )
    print(
        "Shopify variants fetched = matched + missing + dup:  "
        f"{'PASS' if plan.variant_invariant_ok else 'FAIL'}"
    )
    # The write path physically only ever assigns `.image_url` on an
    # already-loaded Product/ProductVariant -- `apply_plan` imports no
    # other model and issues no other statement -- so these are
    # guaranteed by construction, not merely observed here.
    print("No catalog rows created:                             PASS (by construction)")
    print("No inventory quantities changed:                     PASS (by construction)")
    print("No inventory movements changed:                      PASS (by construction)")
    print("No orders changed:                                   PASS (by construction)")
    print("No Shiprocket/RTO data changed:                      PASS (by construction)")
    print("Only image_url may be updated:                       PASS (by construction)")

    verdict = "BLOCKED" if has_blocking_problems(plan) else "PASS"
    print(f"\nVERDICT: {verdict}")


# --- write path (only ever reached under --apply, after a clean plan) -----


async def apply_plan(session: AsyncSession, plan: Plan) -> tuple[int, int]:
    """Sets ONLY `image_url`, ONLY on already-existing rows this plan
    classified `UPDATE`. One commit at the end. Raises (caller does not
    commit) if it is ever handed a blocked plan.
    """
    if has_blocking_problems(plan):
        raise RuntimeError("apply_plan received a blocked plan -- refusing to write.")

    updated_products = 0
    for r in plan.products:
        if r.action != ACTION_UPDATE:
            continue
        product = await session.get(Product, r.oms_id)
        assert product is not None, f"Product {r.oms_id} vanished between plan and apply"
        product.image_url = r.shopify_image_url  # the ONLY attribute this script ever sets
        updated_products += 1

    updated_variants = 0
    for r in plan.variants:
        if r.action != ACTION_UPDATE:
            continue
        variant = await session.get(ProductVariant, r.oms_id)
        assert variant is not None, f"ProductVariant {r.oms_id} vanished between plan and apply"
        variant.image_url = r.shopify_image_url  # the ONLY attribute this script ever sets
        updated_variants += 1

    await session.commit()
    return updated_products, updated_variants


async def _run(*, apply: bool) -> None:
    config = ShopifyConfig.from_settings()
    if config is None:
        print(
            "BLOCKED: Shopify is not configured (need SHOPIFY_STORE_DOMAIN plus either "
            "SHOPIFY_CLIENT_ID/SHOPIFY_CLIENT_SECRET or SHOPIFY_ACCESS_TOKEN). "
            "No database was accessed."
        )
        raise SystemExit(2)

    print(f"Shopify store:   {config.shop_domain}")
    print(f"Database target: {_redacted_db_target()}")
    print()

    adapter = ShopifyAdapter()
    try:
        try:
            await adapter.authenticate()
        except IntegrationError as exc:
            print(
                f"BLOCKED: Shopify authentication failed ({exc.message}). "
                "No database writes performed."
            )
            raise SystemExit(2) from exc

        async with AsyncSessionLocal() as session:
            plan = await build_plan(session, adapter)
            print_report(plan, apply=apply)

            if not apply:
                print(
                    "\n--dry-run (default): no changes made. "
                    "Re-run with --apply only after reviewing the report above."
                )
                return

            if has_blocking_problems(plan):
                print(
                    "\nABORTING --apply: BLOCKED above (missing OMS product/variant, "
                    "duplicate identifier, or Shopify API error). Nothing was written."
                )
                raise SystemExit(1)

            updated_products, updated_variants = await apply_plan(session, plan)
            print(
                f"\nAPPLIED: updated {updated_products} product image(s), "
                f"{updated_variants} variant image(s). (image_url only)"
            )
    finally:
        await adapter.aclose()


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually write image_url updates. Default is dry-run (no writes).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Explicit no-op flag -- dry-run is already the default when --apply is omitted.",
    )
    args = parser.parse_args()
    asyncio.run(run_with_cleanup(_run(apply=args.apply and not args.dry_run)))


if __name__ == "__main__":
    main()

"""One-time, explicit, reviewed data operation: create the OMS-visible
`CatalogVariant` groupings approved for the Inventory "OMS-visible variant"
feature (schema added by migration `d3b8f1e2a5c7` -- schema only, no data).

APPROVED BUSINESS RULE (final, confirmed):

  * ONLY the canonical product "Aayush Wellness Herbal Masala" (Shopify
    product id 8009941287101) gets 3 OMS-visible CatalogVariants (Royal
    Tobacco Flavour / Ghutka Flavour / Paan Masala Flavour).
  * 17 other named products get exactly ONE CatalogVariant each, named
    after the product's own approved display name, grouping ALL of that
    product's existing ProductVariant rows.
  * 4 more named products already have exactly one ProductVariant and get
    NO CatalogVariant row -- `InventoryService.get_oms_variants_for_product`
    already treats a lone, ungrouped ProductVariant as its own implicit
    OMS-visible variant.
  * That is 22 products total. This is a FIXED, REVIEWED list (the
    `MANIFEST` below) -- not a generic "any product with >1 variant" scan.
    A product that is not in `MANIFEST` is NEVER assigned a CatalogVariant
    by this script, no matter how many ProductVariant rows it has; it is
    instead reported as an UNEXPECTED PRODUCT and blocks the run.

SAFE BY DESIGN:
  - DRY-RUN BY DEFAULT. Nothing is written unless `--apply` is passed.
  - MANIFEST-DRIVEN, NOT DISCOVERY-DRIVEN. Every product this script will
    ever touch is resolved by its Shopify product id (a real, immutable
    identifier from the live Shopify store), looked up in the fixed
    `MANIFEST` below -- NEVER by a dev-database UUID, and NEVER by a
    generic "products with >1 variant" query. The same generic query is
    still run, but ONLY to detect and report products that look
    multi-variant yet are absent from the approved manifest (an
    UNEXPECTED PRODUCT), which blocks the run rather than being silently
    included.
  - Only ever writes two things: new `CatalogVariant` rows, and
    `ProductVariant.catalog_variant_id` on rows that are currently NULL.
    Nothing else -- `available_quantity`, `inventory_quantity`, `sku`,
    `shopify_variant_id`, `title`, `title_override`, `OrderItem`,
    `InventoryMovement`, Shiprocket/RTO records, and Shopify sync
    behaviour are never read for a decision here and never touched. The
    known pre-existing negative `available_quantity` rows are left as-is.
  - Idempotent: a `CatalogVariant` is looked up by `(product_id, name)`
    before creating one; a `ProductVariant` whose `catalog_variant_id`
    already points at the correct target is left alone (rowcount 0).
    Running this script twice in a row is a no-op the second time.
  - Never silently overwrites a surprising existing assignment: any
    `ProductVariant` whose `catalog_variant_id` is already set to a
    DIFFERENT CatalogVariant than the one this plan would assign is
    reported as a CONFLICT and the whole run aborts (dry-run or apply)
    -- no partial write.
  - Never guesses: a manifest product whose actual ProductVariant rows
    (by SKU + Shopify variant id) don't exactly match what the manifest
    expects -- missing rows, extra/unexpected rows, a title mismatch, or
    (for the canonical product) a variant that matches none of the three
    approved SKU prefixes -- blocks the whole run. Nothing is
    auto-assigned or auto-guessed for it.
  - `--apply` runs the entire write in ONE transaction and only commits
    if every row in the plan applies cleanly and the plan is unblocked;
    any error rolls back everything (session is never committed on
    failure/abort).

Run with:
    python scripts/backfill_catalog_variants.py                # dry-run (default)
    python scripts/backfill_catalog_variants.py --dry-run       # same, explicit
    python scripts/backfill_catalog_variants.py --apply         # REAL WRITE -- only
                                                                  # after reviewing the
                                                                  # dry-run output above

Run this from a Render Shell session (API or worker service -- both have
DB access), or locally against the dev database, exactly like the other
one-off scripts in this directory (see `reset_shopify_orders_backlog.py`).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

# Run as `python scripts/backfill_catalog_variants.py` -- see the matching
# comment in `reset_shopify_orders_backlog.py` for why this path insert is
# required (a plain `python some_script.py` invocation, unlike uvicorn/
# celery, does not put the repo root on sys.path on its own).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.logging import configure_logging, get_logger  # noqa: E402
from app.db.session import AsyncSessionLocal, run_with_cleanup  # noqa: E402
from app.models.product import CatalogVariant, Product, ProductVariant  # noqa: E402
from sqlalchemy import func, select  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

configure_logging()
logger = get_logger(__name__)

# --- the approved 22-product manifest (business identifiers only) --------
#
# This is the ONLY set of products this script will ever assign a
# CatalogVariant to. Built from a read-only inspection of the dev database
# and reviewed/approved turn-by-turn. Keyed by Shopify product id.


@dataclass(frozen=True)
class ManifestVariant:
    sku: str
    shopify_variant_id: str
    catalog_variant_name: str  # which OMS-visible group this SKU belongs to


@dataclass(frozen=True)
class ManifestProduct:
    shopify_product_id: str
    expected_title: str
    expected_variant_count: int
    expected_oms_variant_count: int  # 1 or 3
    # Ordered, de-duplicated names of the CatalogVariant row(s) this
    # product should end up with. Empty for the 4 already-single-variant
    # products -- they need no CatalogVariant row at all.
    catalog_variant_names: tuple[str, ...]
    # Full expected ProductVariant roster (sku, shopify_variant_id, target
    # group name) for the 18 multi-variant products. Left empty for the 4
    # single-variant products (no per-SKU roster captured for them since
    # no grouping decision is needed) -- expected_variant_count == 1 is
    # still checked for those.
    variants: tuple[ManifestVariant, ...] = ()


_CANONICAL_VARIANTS: tuple[ManifestVariant, ...] = (
    ManifestVariant("AW-HM-CR-120", "45082735378621", "Ghutka Flavour"),
    ManifestVariant("AW-HM-CR-180", "46521815662781", "Ghutka Flavour"),
    ManifestVariant("AW-HM-CR-60", "45082739605693", "Ghutka Flavour"),
    ManifestVariant("AW-HM-PN-120", "45082735280317", "Paan Masala Flavour"),
    ManifestVariant("AW-HM-PN-180", "46521815630013", "Paan Masala Flavour"),
    ManifestVariant("AW-HM-PN-60", "45082739572925", "Paan Masala Flavour"),
    ManifestVariant("AW-HM-RG-120", "45082735182013", "Royal Tobacco Flavour"),
    ManifestVariant("AW-HM-RG-180", "46521815695549", "Royal Tobacco Flavour"),
    ManifestVariant("AW-HM-RG-60", "45082739540157", "Royal Tobacco Flavour"),
)

# Fixed OMS-variant display order for the canonical product.
CANONICAL_FLAVOUR_ORDER: dict[str, int] = {
    "Royal Tobacco Flavour": 0,
    "Ghutka Flavour": 1,
    "Paan Masala Flavour": 2,
}


def _single_group(name: str, variants: tuple[tuple[str, str], ...]) -> tuple[ManifestVariant, ...]:
    return tuple(ManifestVariant(sku, svid, name) for sku, svid in variants)


MANIFEST: tuple[ManifestProduct, ...] = (
    # --- the canonical product: 3-way flavour split ---
    ManifestProduct(
        shopify_product_id="8009941287101",
        expected_title="Aayush Wellness Herbal Masala",
        expected_variant_count=9,
        expected_oms_variant_count=3,
        catalog_variant_names=("Royal Tobacco Flavour", "Ghutka Flavour", "Paan Masala Flavour"),
        variants=_CANONICAL_VARIANTS,
    ),
    # --- 17 other multi-variant products: exactly 1 CatalogVariant each ---
    ManifestProduct(
        "7972941660349",
        "Aayush Wellness Herbal Masala New - Ziplock Big Pouches!",
        9,
        1,
        ("Aayush Wellness Herbal Masala New - Ziplock Big Pouches!",),
        _single_group(
            "Aayush Wellness Herbal Masala New - Ziplock Big Pouches!",
            (
                ("AW-HM-CR-100g", "44894215962813"),
                ("AW-HM-CR-150g", "44894215995581"),
                ("AW-HM-CR-300g", "44894216028349"),
                ("AW-HM-PN-100g", "45111266541757"),
                ("AW-HM-PN-150g", "44894216093885"),
                ("AW-HM-PN-300g", "44894216126653"),
                ("AW-HM-RG-100g", "44894216159421"),
                ("AW-HM-RG-150g", "44894216192189"),
                ("AW-HM-RG-300g", "44894216224957"),
            ),
        ),
    ),
    ManifestProduct(
        "8103466631357",
        "Aayush Herbal Masala (New)",
        6,
        1,
        ("Aayush Herbal Masala (New)",),
        _single_group(
            "Aayush Herbal Masala (New)",
            (
                ("shopify-45771790581949", "45771790581949"),
                ("shopify-45771790549181", "45771790549181"),
                ("shopify-45771790680253", "45771790680253"),
                ("shopify-45771790647485", "45771790647485"),
                ("shopify-45771790778557", "45771790778557"),
                ("shopify-45771790745789", "45771790745789"),
            ),
        ),
    ),
    ManifestProduct(
        "8398327546045",
        "Aayush Wellness Herbal Masala Bulk Order",
        6,
        1,
        ("Aayush Wellness Herbal Masala Bulk Order",),
        _single_group(
            "Aayush Wellness Herbal Masala Bulk Order",
            (
                ("AW-HM-CR-21", "47647499944125"),
                ("AW-HM-CR-35", "47647515934909"),
                ("AW-HM-PN-21", "47647499845821"),
                ("AW-HM-PN-35", "47647515902141"),
                ("AW-HM-RG-21", "47647500042429"),
                ("AW-HM-RG-35", "47647515967677"),
            ),
        ),
    ),
    ManifestProduct(
        "8182639329469",
        "Test AHM draft testings",
        6,
        1,
        ("Test AHM draft testings",),
        _single_group(
            "Test AHM draft testings",
            (
                ("shopify-46285664485565", "46285664485565"),
                ("shopify-46285664452797", "46285664452797"),
                ("shopify-46285664551101", "46285664551101"),
                ("shopify-46285664518333", "46285664518333"),
                ("shopify-46285664616637", "46285664616637"),
                ("shopify-46285664583869", "46285664583869"),
            ),
        ),
    ),
    ManifestProduct(
        "8333928792253",
        "Testing",
        6,
        1,
        ("Testing",),
        _single_group(
            "Testing",
            (
                ("shopify-47743177228477", "47743177228477"),
                ("shopify-47743177261245", "47743177261245"),
                ("shopify-47624908243133", "47624908243133"),
                ("shopify-47743177294013", "47743177294013"),
                ("shopify-47743177326781", "47743177326781"),
                ("shopify-47713124843709", "47713124843709"),
            ),
        ),
    ),
    ManifestProduct(
        "8475914043581",
        "Arjuna Plus",
        3,
        1,
        ("Arjuna Plus",),
        _single_group(
            "Arjuna Plus",
            (
                ("ARJ-PLS-30", "48337109582013"),
                ("ARJ-PLS-60", "48337109614781"),
                ("ARJ-PLS-90", "48337109647549"),
            ),
        ),
    ),
    ManifestProduct(
        "8075023974589",
        "Brain Fuel Capsules",
        3,
        1,
        ("Brain Fuel Capsules",),
        _single_group(
            "Brain Fuel Capsules",
            (
                ("AW-BF-CP-30", "45746559058109"),
                ("AW-BF-CP-60", "45746559090877"),
                ("AW-BF-CP-90", "45746559123645"),
            ),
        ),
    ),
    ManifestProduct(
        "8075024335037",
        "Calcium+ Vitamins Tablets",
        3,
        1,
        ("Calcium+ Vitamins Tablets",),
        _single_group(
            "Calcium+ Vitamins Tablets",
            (
                ("AW-CV-TB-30", "45793484177597"),
                ("AW-CV-TB-60", "45793484210365"),
                ("AW-CV-TB-90", "45793484243133"),
            ),
        ),
    ),
    ManifestProduct(
        "8075024695485",
        "Dia Shield Tablets",
        3,
        1,
        ("Dia Shield Tablets",),
        _single_group(
            "Dia Shield Tablets",
            (
                ("AW-DS-TB-20", "45766595150013"),
                ("AW-DS-TB-40", "45766595182781"),
                ("AW-DS-TB-60", "45766595215549"),
            ),
        ),
    ),
    ManifestProduct(
        "8005829656765",
        "Dreamy Sleep Gummies",
        3,
        1,
        ("Dreamy Sleep Gummies",),
        _single_group(
            "Dreamy Sleep Gummies",
            (
                ("AW-SG-GM-30", "45068786041021"),
                ("AW-SG-GM-60", "45068786073789"),
                ("AW-SG-GM-90", "45579410571453"),
            ),
        ),
    ),
    ManifestProduct(
        "8075024990397",
        "Gut Fuel Capsules",
        3,
        1,
        ("Gut Fuel Capsules",),
        _single_group(
            "Gut Fuel Capsules",
            (
                ("AW-GF-CP-30", "45793466450109"),
                ("AW-GF-CP-60", "45793466482877"),
                ("AW-GF-CP-90", "45793466515645"),
            ),
        ),
    ),
    ManifestProduct(
        "8075025088701",
        "Immune Care Tablets",
        3,
        1,
        ("Immune Care Tablets",),
        _single_group(
            "Immune Care Tablets",
            (
                ("AW-IC-TB-30", "45764758241469"),
                ("AW-IC-TB-60", "45764758274237"),
                ("AW-IC-TB-90", "45764758307005"),
            ),
        ),
    ),
    ManifestProduct(
        "8075025121469",
        "Liver Detox Tablets",
        3,
        1,
        ("Liver Detox Tablets",),
        _single_group(
            "Liver Detox Tablets",
            (
                ("AW-LD-TB-30", "45792959135933"),
                ("AW-LD-TB-60", "45792959168701"),
                ("AW-LD-TB-90", "45792959201469"),
            ),
        ),
    ),
    ManifestProduct(
        "8075025154237",
        "Lung Care Tablets",
        3,
        1,
        ("Lung Care Tablets",),
        _single_group(
            "Lung Care Tablets",
            (
                ("AW-LC-TB-30", "45781603418301"),
                ("AW-LC-TB-60", "45781603451069"),
                ("AW-LC-TB-90", "45781603483837"),
            ),
        ),
    ),
    ManifestProduct(
        "8471325999293",
        "Vajrashakti",
        3,
        1,
        ("Vajrashakti",),
        _single_group(
            "Vajrashakti",
            (
                ("VJR-SKT-30", "48322406187197"),
                ("VJR-SKT-60", "48322406219965"),
                ("VJR-SKT-90", "48322406252733"),
            ),
        ),
    ),
    ManifestProduct(
        "8310135521469",
        "Himalayan Shilajit Drops",
        2,
        1,
        ("Himalayan Shilajit Drops",),
        _single_group(
            "Himalayan Shilajit Drops",
            (
                ("AW-HS-DP-30", "46917889851581"),
                ("AW-HS-DP-60", "46917889884349"),
            ),
        ),
    ),
    ManifestProduct(
        "8005829886141",
        "Skin, Hair & Nail Gummies with Glutathione & Hyaluronic Acid",
        2,
        1,
        ("Skin, Hair & Nail Gummies with Glutathione & Hyaluronic Acid",),
        _single_group(
            "Skin, Hair & Nail Gummies with Glutathione & Hyaluronic Acid",
            (
                ("AW-BV-GM-30", "45068786335933"),
                ("AW-BV-GM-60", "45068786368701"),
            ),
        ),
    ),
    # --- 4 products already at exactly 1 ProductVariant: no CatalogVariant
    #     row expected at all. Roster not captured (no grouping decision
    #     needed); only the count is verified.
    ManifestProduct("8210784059581", "Gut & Detox Bundle", 1, 1, ()),
    ManifestProduct("8210784354493", "Herbal Masala Trial Bundle", 1, 1, ()),
    ManifestProduct("8207039135933", "Immunity & Vitality Bundle", 1, 1, ()),
    ManifestProduct("8210784157885", "Mind & Lifestyle Balance Bundle", 1, 1, ()),
)

assert len(MANIFEST) == 22, f"MANIFEST must have exactly 22 approved products, has {len(MANIFEST)}"
_MANIFEST_BY_SPID: dict[str, ManifestProduct] = {m.shopify_product_id: m for m in MANIFEST}
assert len(_MANIFEST_BY_SPID) == 22, "duplicate shopify_product_id in MANIFEST"


# --- plan data structures (pure; no DB writes happen while building these) -


@dataclass
class VariantAssignment:
    product_variant_id: object  # uuid.UUID
    sku: str
    shopify_variant_id: str | None
    available_quantity: int
    current_catalog_variant_id: object | None
    proposed_catalog_variant_name: str
    already_correct: bool = False


@dataclass
class CatalogVariantPlan:
    product_id: object  # uuid.UUID
    product_title: str
    shopify_product_id: str
    name: str
    display_order: int
    existing_catalog_variant_id: object | None  # set if (product_id, name) already exists
    members: list[VariantAssignment] = field(default_factory=list)


@dataclass
class Plan:
    approved_product_count: int = len(MANIFEST)
    discovered_product_count: int = 0
    found_manifest_products: int = 0
    missing_manifest_products: list[str] = field(default_factory=list)  # "title (spid)"
    title_mismatches: list[tuple[str, str, str]] = field(
        default_factory=list
    )  # (spid, expected_title, actual_title)
    unexpected_products: list[tuple[str, str, int]] = field(
        default_factory=list
    )  # (title, shopify_product_id, variant_count) -- multi-variant, not in MANIFEST
    variant_count_mismatches: list[tuple[str, int, int]] = field(
        default_factory=list
    )  # (title, expected, actual)
    unexpected_variants: list[tuple[str, str, str]] = field(
        default_factory=list
    )  # (product title, sku, shopify_variant_id) -- present in DB, not in manifest roster
    missing_variants: list[tuple[str, str, str]] = field(
        default_factory=list
    )  # (product title, sku, shopify_variant_id) -- in manifest roster, absent from DB
    catalog_variant_plans: list[CatalogVariantPlan] = field(default_factory=list)
    unmapped_variants: list[tuple[str, str, str]] = field(default_factory=list)
    conflicts: list[tuple[str, str, object, object]] = field(default_factory=list)
    duplicate_skus: list[tuple[str, int]] = field(default_factory=list)
    duplicate_shopify_variant_ids: list[tuple[str, int]] = field(default_factory=list)


async def _duplicate_check(
    session: AsyncSession,
) -> tuple[list[tuple[str, int]], list[tuple[str, int]]]:
    """Global sanity check -- the DB already enforces uniqueness on both
    columns, so this is expected to always come back empty; it is run and
    reported explicitly anyway, per the requested preflight checklist.
    """
    sku_rows = (
        await session.execute(
            select(ProductVariant.sku, func.count())
            .group_by(ProductVariant.sku)
            .having(func.count() > 1)
        )
    ).all()
    svid_rows = (
        await session.execute(
            select(ProductVariant.shopify_variant_id, func.count())
            .where(ProductVariant.shopify_variant_id.is_not(None))
            .group_by(ProductVariant.shopify_variant_id)
            .having(func.count() > 1)
        )
    ).all()
    return [(r[0], r[1]) for r in sku_rows], [(r[0], r[1]) for r in svid_rows]


async def build_plan(session: AsyncSession) -> Plan:
    """Pure, read-only. Resolves every one of the 22 approved manifest
    products by Shopify product id, verifies each against its expected
    roster, and separately flags any *other* multi-variant product found
    in the database that is not on the manifest. Never writes anything.
    """
    plan = Plan()

    dup_skus, dup_svids = await _duplicate_check(session)
    plan.duplicate_skus = dup_skus
    plan.duplicate_shopify_variant_ids = dup_svids

    # --- 1. resolve every approved manifest product by shopify_product_id -
    for manifest_product in MANIFEST:
        db_product = (
            await session.execute(
                select(Product).where(
                    Product.shopify_product_id == manifest_product.shopify_product_id
                )
            )
        ).scalar_one_or_none()

        if db_product is None:
            plan.missing_manifest_products.append(
                f"{manifest_product.expected_title} ({manifest_product.shopify_product_id})"
            )
            continue

        plan.found_manifest_products += 1
        plan.discovered_product_count += 1

        if db_product.title != manifest_product.expected_title:
            plan.title_mismatches.append(
                (
                    manifest_product.shopify_product_id,
                    manifest_product.expected_title,
                    db_product.title,
                )
            )

        actual_variants = list(
            (
                await session.execute(
                    select(ProductVariant).where(ProductVariant.product_id == db_product.id)
                )
            )
            .scalars()
            .all()
        )

        if len(actual_variants) != manifest_product.expected_variant_count:
            plan.variant_count_mismatches.append(
                (
                    manifest_product.expected_title,
                    manifest_product.expected_variant_count,
                    len(actual_variants),
                )
            )

        # --- roster cross-check (only for the 18 products with a captured
        #     roster; the 4 single-variant products only get a count check)
        expected_by_sku = {v.sku: v for v in manifest_product.variants}
        actual_by_sku = {v.sku: v for v in actual_variants}

        if manifest_product.variants:
            for sku, expected_v in expected_by_sku.items():
                actual_v = actual_by_sku.get(sku)
                if actual_v is None or actual_v.shopify_variant_id != expected_v.shopify_variant_id:
                    plan.missing_variants.append(
                        (manifest_product.expected_title, sku, expected_v.shopify_variant_id)
                    )
            for sku, actual_v in actual_by_sku.items():
                if sku not in expected_by_sku:
                    plan.unexpected_variants.append(
                        (
                            manifest_product.expected_title,
                            sku,
                            actual_v.shopify_variant_id or "",
                        )
                    )

        if not manifest_product.catalog_variant_names:
            continue  # one of the 4 -- no CatalogVariant row expected

        existing_cvs = {
            cv.name: cv
            for cv in (
                await session.execute(
                    select(CatalogVariant).where(CatalogVariant.product_id == db_product.id)
                )
            )
            .scalars()
            .all()
        }

        for name in manifest_product.catalog_variant_names:
            existing_cv = existing_cvs.get(name)
            display_order = (
                CANONICAL_FLAVOUR_ORDER[name]
                if manifest_product.expected_oms_variant_count == 3
                else 0
            )
            cv_plan = CatalogVariantPlan(
                product_id=db_product.id,
                product_title=db_product.title,
                shopify_product_id=manifest_product.shopify_product_id,
                name=name,
                display_order=display_order,
                existing_catalog_variant_id=existing_cv.id if existing_cv else None,
            )
            for sku, expected_v in expected_by_sku.items():
                if expected_v.catalog_variant_name != name:
                    continue
                actual_v = actual_by_sku.get(sku)
                if actual_v is None:
                    continue  # already reported as a missing variant above
                cv_plan.members.append(
                    _classify_member(actual_v, existing_cv, plan, manifest_product.expected_title)
                )
            plan.catalog_variant_plans.append(cv_plan)

        # A canonical-roster variant that exists in the DB but couldn't be
        # classified into any of the three named groups would already show
        # up as an `unexpected_variant` above (its SKU is not in
        # `expected_by_sku` at all); nothing further to do here -- there is
        # no separate "SKU prefix didn't match" case any more because the
        # manifest enumerates every accepted SKU explicitly.

    # --- 2. flag any *other* multi-variant product NOT in the manifest ---
    generic_multi_variant = (
        await session.execute(
            select(
                Product.id,
                Product.title,
                Product.shopify_product_id,
                func.count(ProductVariant.id).label("n"),
            )
            .join(ProductVariant, ProductVariant.product_id == Product.id)
            .group_by(Product.id, Product.title, Product.shopify_product_id)
            .having(func.count(ProductVariant.id) > 1)
        )
    ).all()

    for _product_id, title, shopify_product_id, n in generic_multi_variant:
        if shopify_product_id in _MANIFEST_BY_SPID:
            continue  # already handled above
        plan.unexpected_products.append((title, shopify_product_id or "", n))
        plan.discovered_product_count += 1

    return plan


def _classify_member(
    variant: ProductVariant,
    existing_cv: CatalogVariant | None,
    plan: Plan,
    product_title: str,
) -> VariantAssignment:
    current = variant.catalog_variant_id
    target_existing_id = existing_cv.id if existing_cv else None

    if current is not None and current != target_existing_id:
        # Already grouped under something else entirely -- never overwrite.
        plan.conflicts.append((product_title, variant.sku, current, target_existing_id))

    already_correct = existing_cv is not None and current == existing_cv.id
    return VariantAssignment(
        product_variant_id=variant.id,
        sku=variant.sku,
        shopify_variant_id=variant.shopify_variant_id,
        available_quantity=variant.available_quantity,
        current_catalog_variant_id=current,
        proposed_catalog_variant_name=existing_cv.name if existing_cv else "(new)",
        already_correct=already_correct,
    )


# --- reporting --------------------------------------------------------


def print_report(plan: Plan) -> None:
    print("=" * 78)
    print("CatalogVariant backfill -- DRY-RUN REPORT (manifest-driven)")
    print("=" * 78)

    print(f"\nApproved manifest products:                 {plan.approved_product_count}")
    print(f"Found in database:                           {plan.found_manifest_products}")
    print("Missing (approved, not found in database):")
    print(f"  {len(plan.missing_manifest_products)}")
    for entry in plan.missing_manifest_products:
        print(f"  - MISSING APPROVED PRODUCT: {entry}")
    print(f"Title mismatches (approved vs actual DB title): {len(plan.title_mismatches)}")
    for spid, expected, actual in plan.title_mismatches:
        print(
            f"  - TITLE MISMATCH: shopify_product_id={spid} "
            f"expected={expected!r} actual={actual!r}"
        )

    print(
        f"\nUnexpected products (>1 variant, NOT on the approved manifest): "
        f"{len(plan.unexpected_products)}"
    )
    for title, spid, n in plan.unexpected_products:
        print(f"  - UNEXPECTED PRODUCT: {title!r} shopify_product_id={spid} variant_count={n}")

    print(f"\nProductVariant count mismatches: {len(plan.variant_count_mismatches)}")
    for title, expected, actual in plan.variant_count_mismatches:
        print(f"  - COUNT MISMATCH: product={title!r} expected={expected} actual={actual}")
    print(
        f"Missing ProductVariants (expected by manifest, absent in DB): "
        f"{len(plan.missing_variants)}"
    )
    for title, sku, svid in plan.missing_variants:
        print(f"  - MISSING VARIANT: product={title!r} sku={sku!r} shopify_variant_id={svid!r}")
    print(
        f"Unexpected ProductVariants (present in DB, not on the manifest roster): "
        f"{len(plan.unexpected_variants)}"
    )
    for title, sku, svid in plan.unexpected_variants:
        print(f"  - UNEXPECTED VARIANT: product={title!r} sku={sku!r} shopify_variant_id={svid!r}")

    for cv in plan.catalog_variant_plans:
        is_canonical = cv.shopify_product_id == "8009941287101"
        tag = " [CANONICAL 3-WAY SPLIT]" if is_canonical else ""
        print(
            f"\n--- Product: {cv.product_title!r} "
            f"(shopify_product_id={cv.shopify_product_id}){tag}"
        )
        print(f"    -> CatalogVariant: {cv.name!r}  (display_order={cv.display_order})")
        state = "ALREADY EXISTS" if cv.existing_catalog_variant_id else "TO BE CREATED"
        print(
            f"       {state}"
            + (f" (id={cv.existing_catalog_variant_id})" if cv.existing_catalog_variant_id else "")
        )
        if not cv.members:
            print("       (no underlying ProductVariant rows matched -- nothing to assign)")
        for m in cv.members:
            status = "already assigned" if m.already_correct else "TO ASSIGN"
            print(
                f"       - PV {m.product_variant_id}  sku={m.sku!r}  "
                f"shopify_variant_id={m.shopify_variant_id!r}  "
                f"available_quantity={m.available_quantity}  "
                f"current_catalog_variant_id={m.current_catalog_variant_id}  "
                f"proposed_catalog_variant={cv.name!r}  [{status}]"
            )

    to_create = sum(
        1 for cv in plan.catalog_variant_plans if cv.existing_catalog_variant_id is None
    )
    already_exist = len(plan.catalog_variant_plans) - to_create
    total_members = sum(len(cv.members) for cv in plan.catalog_variant_plans)
    to_assign = sum(
        1 for cv in plan.catalog_variant_plans for m in cv.members if not m.already_correct
    )
    already_assigned = total_members - to_assign
    duplicate_identifiers = len(plan.duplicate_skus) + len(plan.duplicate_shopify_variant_ids)

    print("\n" + "-" * 78)
    print("SUMMARY")
    print("-" * 78)
    print(f"APPROVED PRODUCTS: {plan.approved_product_count}")
    print(f"DISCOVERED PRODUCTS: {plan.discovered_product_count}")
    print(f"UNEXPECTED PRODUCTS: {len(plan.unexpected_products)}")
    print(f"MISSING APPROVED PRODUCTS: {len(plan.missing_manifest_products)}")
    print()
    print(f"CATALOG VARIANTS TO CREATE: {to_create}")
    print(f"  (already exist, idempotent no-op: {already_exist})")
    print(f"PRODUCT VARIANT ASSIGNMENTS: {to_assign}")
    print(
        f"  (already correctly assigned: {already_assigned}; "
        f"total planned members: {total_members})"
    )
    print()
    print(f"UNMAPPED VARIANTS: {len(plan.unmapped_variants)}")
    for title, sku, svid in plan.unmapped_variants:
        print(f"  - {title!r} sku={sku!r} shopify_variant_id={svid!r}")
    print(f"CONFLICTS: {len(plan.conflicts)}")
    for title, sku, current, proposed in plan.conflicts:
        print(
            f"  - product={title!r} sku={sku!r} current_catalog_variant_id={current} "
            f"proposed_target={proposed}"
        )
    print(f"DUPLICATE IDENTIFIERS: {duplicate_identifiers}")
    for sku, n in plan.duplicate_skus:
        print(f"  - DUPLICATE SKU: {sku!r} x{n}")
    for svid, n in plan.duplicate_shopify_variant_ids:
        print(f"  - DUPLICATE shopify_variant_id: {svid!r} x{n}")
    print(f"UNEXPECTED VARIANTS: {len(plan.unexpected_variants)}")

    print("\n" + "-" * 78)
    print("FINAL INVARIANT")
    print("-" * 78)
    counts = Counter(cv.shopify_product_id for cv in plan.catalog_variant_plans)
    canonical_ok = counts.get("8009941287101", 0) == 3
    others_ok = all(n == 1 for spid, n in counts.items() if spid != "8009941287101")
    no_extra_cv_products = set(counts.keys()) <= set(_MANIFEST_BY_SPID.keys())
    singles_ok = all(
        counts.get(m.shopify_product_id, 0) == 0 for m in MANIFEST if not m.catalog_variant_names
    )
    blocked = has_blocking_problems(plan)

    print(f"Aayush Wellness Herbal Masala = 3 OMS variants:        {canonical_ok}")
    print(f"Every other approved product = 1 OMS variant:          {others_ok}")
    print(f"4 single-variant products get no CatalogVariant row:   {singles_ok}")
    print(f"No unapproved product receives a CatalogVariant:       {no_extra_cv_products}")

    invariant_ok = canonical_ok and others_ok and singles_ok and no_extra_cv_products
    verdict = "PASS" if (not blocked and invariant_ok) else "BLOCKED"
    action = (
        "safe to review for --apply"
        if verdict == "PASS"
        else "DO NOT APPLY -- resolve issues above"
    )
    print(f"\n{verdict} -- {action}")


def has_blocking_problems(plan: Plan) -> bool:
    return bool(
        plan.missing_manifest_products
        or plan.title_mismatches
        or plan.unexpected_products
        or plan.variant_count_mismatches
        or plan.missing_variants
        or plan.unexpected_variants
        or plan.unmapped_variants
        or plan.conflicts
        or plan.duplicate_skus
        or plan.duplicate_shopify_variant_ids
    )


# --- write path (only ever called under --apply, after a clean plan) ------


async def apply_plan(session: AsyncSession, plan: Plan) -> None:
    """Writes exactly what `plan` describes: create-if-missing
    CatalogVariant rows, then set `catalog_variant_id` on every
    ProductVariant currently NULL that this plan targets. One
    transaction; raises (and the caller does not commit) on any
    surprise, leaving the database exactly as it was.
    """
    created = 0
    assigned = 0
    for cv_plan in plan.catalog_variant_plans:
        if cv_plan.existing_catalog_variant_id is not None:
            catalog_variant_id = cv_plan.existing_catalog_variant_id
        else:
            cv = CatalogVariant(
                product_id=cv_plan.product_id,
                name=cv_plan.name,
                display_order=cv_plan.display_order,
            )
            session.add(cv)
            await session.flush()  # assigns cv.id, does not commit
            catalog_variant_id = cv.id
            created += 1

        for member in cv_plan.members:
            if member.already_correct:
                continue
            if member.current_catalog_variant_id is not None:
                # Safety net -- build_plan() already refused to reach here
                # via `has_blocking_problems`, but never write past a
                # surprise assignment even if this function is ever
                # called directly.
                raise RuntimeError(
                    f"Refusing to overwrite existing catalog_variant_id on "
                    f"ProductVariant {member.product_variant_id} (sku={member.sku!r})."
                )
            variant = await session.get(ProductVariant, member.product_variant_id)
            assert variant is not None
            variant.catalog_variant_id = catalog_variant_id
            assigned += 1

    await session.commit()
    print(f"\nAPPLIED: created {created} CatalogVariant row(s), assigned {assigned} PV row(s).")


async def _run(*, apply: bool) -> None:
    async with AsyncSessionLocal() as session:
        plan = await build_plan(session)
        print_report(plan)

        if not apply:
            print("\n--dry-run (default): no changes made. Re-run with --apply only after review.")
            return

        if has_blocking_problems(plan):
            print(
                "\nABORTING --apply: BLOCKED above (missing/unexpected product, title or "
                "variant-roster mismatch, conflicting assignment, or duplicate SKU/"
                "shopify_variant_id). No changes were made."
            )
            raise SystemExit(1)

        await apply_plan(session, plan)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually write the CatalogVariant rows and assignments. Default is dry-run.",
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

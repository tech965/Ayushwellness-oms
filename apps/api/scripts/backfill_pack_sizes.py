"""One-time, explicit, reviewed data operation: set the correct
`ProductVariant.pack_size` for the canonical "Aayush Wellness Herbal
Masala" product's real pack-size SKUs (added by migration
`d4f8a3c1e6b9` -- schema only, every row defaults to 1, no data).

ROOT CAUSE this fixes: `pack_size` (and `packets_per_box`) have never
been configured for any real variant -- every row is still at its
default of 1. `InventoryService.apply_dispatch`'s formula, `ceil
(quantity * pack_size / packets_per_box)`, therefore collapses to
`quantity` for every SKU regardless of its real pack size -- a 120-Pack
shipment deducts -1 box (matching `quantity`), not the correct -2.

APPROVED BUSINESS RULE (this script only supplies correct DATA, never
changes `InventoryService`'s deduction formula, which stays generic):
    60 Pack  -> 1 box  per unit ordered
    120 Pack -> 2 boxes per unit ordered
    180 Pack -> 3 boxes per unit ordered
Expressed here as an explicit table (`_PACK_SIZE_TO_BOXES`), never
computed from the pouch count via a formula change -- this business
rule has already been revised once (a prior version of this script used
120 -> 3), and keeping it as one small, explicitly reviewed table is
what makes a future revision a one-line diff instead of a logic change.

Kept `packets_per_box` at its default of 1 for every one of these rows
(never touched by this script) -- the formula above then reduces to
`boxes = quantity * pack_size`, i.e. `pack_size` directly IS the
box-count this variant's unit consumes.

WHY LIVE DISCOVERY BY PRODUCT ID, NOT A FIXED SKU-STRING ROSTER: an
earlier draft of this script hardcoded the exact SKU strings from
`backfill_catalog_variants.py`'s `_CANONICAL_VARIANTS` comment -- that
promptly failed against this environment's database, because Shopify
sync appends a `-shopify-<id>` fallback suffix to a SKU only once a
real collision has actually happened (`ProductService._safe_sku`), and
that had happened for SOME environments/points in time but not others.
Hardcoding the exact string was exactly the kind of brittleness this
task's "do not hardcode SKU names" instruction warns against. Instead,
this script resolves ONLY the canonical product by its stable Shopify
product id (`CANONICAL_SHOPIFY_PRODUCT_ID`, a real, immutable business
identifier -- the same one already used for this exact purpose by the
frontend's `CANONICAL_HERBAL_MASALA_SHOPIFY_PRODUCT_ID` and by
`backfill_catalog_variants.py`), then reads EVERY real `ProductVariant`
row under that ONE product live, and parses each one's pouch count
generically from its SKU (`AW-HM-<flavour>-<N>[-shopify-<id>]`) or,
when the SKU is a synthetic `shopify-<id>` fallback with no embedded
number, from its `title` (`"<Flavour> / <N> - Pouches"`). A variant
whose SKU and title BOTH fail to parse a recognised pouch count blocks
the run -- never guessed, never silently skipped.

Any OTHER product needing this same kind of pack-size configuration
should go through the existing staff-facing
`PATCH /inventory/stock/{variant_id}/pack-size` action instead (see
`InventoryService.update_pack_size`), one variant at a time, reviewed
by a human who knows that product's real packaging -- this script only
ever touches the one canonical product above, never a catalog-wide
sweep.

SAFE BY DESIGN:
  - DRY-RUN BY DEFAULT. Nothing is written unless `--apply` is passed.
  - SCOPED TO EXACTLY ONE PRODUCT, by its immutable Shopify product id
    -- never a generic "any product whose SKU looks like a pack size"
    scan across the whole catalog.
  - A variant under that product whose pouch count can't be parsed
    from either its SKU or its title, or whose pouch count has no
    entry in `_PACK_SIZE_TO_BOXES`, blocks the whole run (reported,
    never guessed/skipped silently).
  - A variant whose `pack_size` is ALREADY correct is left alone
    (idempotent no-op) -- running this twice in a row makes no further
    changes the second time.
  - Writes ONLY `pack_size`, via the existing, audited
    `InventoryService.update_pack_size` (same code path as a manual
    staff edit through the settings UI) -- `available_quantity`,
    `packets_per_box`, `InventoryMovement`, Shiprocket/RTO records, and
    Shopify sync behaviour are never read for a decision here and never
    touched.

Run with:
    python scripts/backfill_pack_sizes.py                # dry-run (default)
    python scripts/backfill_pack_sizes.py --dry-run       # same, explicit
    python scripts/backfill_pack_sizes.py --apply         # REAL WRITE -- only
                                                             after reviewing the
                                                             dry-run output above

Run this from a Render Shell session (API or worker service -- both have
DB access), or locally against the dev database, exactly like the other
one-off scripts in this directory (see `backfill_catalog_variants.py`).
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.logging import configure_logging, get_logger  # noqa: E402
from app.db.session import AsyncSessionLocal, run_with_cleanup  # noqa: E402
from app.models.product import Product, ProductVariant  # noqa: E402
from app.services.inventory_service import InventoryService  # noqa: E402
from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

configure_logging()
logger = get_logger(__name__)

CANONICAL_SHOPIFY_PRODUCT_ID = "8009941287101"

# Documented business mapping (pouch count -> boxes consumed per unit
# ordered) -- an explicit table, never a computed ratio, so a future
# revision (this rule has already changed once) is a one-line edit here
# rather than a change to `InventoryService`'s deduction formula.
_PACK_SIZE_TO_BOXES: dict[int, int] = {60: 1, 120: 2, 180: 3}

# Primary: the pouch count embedded in the SKU itself
# (`AW-HM-<flavour>-<N>[-shopify-<id>]`).
_SKU_PACK_SIZE_RE = re.compile(r"^AW-HM-[A-Z]+-(\d+)(?:-shopify-\d+)?$")
# Fallback: a synthetic `shopify-<id>` SKU (no embedded number) still
# carries the real pouch count in its title, e.g.
# "Paan Masala Flavour / 180 - Pouches".
_TITLE_PACK_SIZE_RE = re.compile(r"(\d+)\s*-\s*Pouches", re.IGNORECASE)


@dataclass
class PlanRow:
    sku: str
    title: str | None
    product_variant_id: uuid.UUID
    current_pack_size: int
    target_pack_size: int | None  # None only if no pouch count could be parsed/mapped
    pouch_count: int | None
    parsed_from: str  # "sku" | "title" | "unparsed"


def _parse_pouch_count(sku: str, title: str | None) -> tuple[int | None, str]:
    match = _SKU_PACK_SIZE_RE.match(sku)
    if match:
        return int(match.group(1)), "sku"
    if title:
        match = _TITLE_PACK_SIZE_RE.search(title)
        if match:
            return int(match.group(1)), "title"
    return None, "unparsed"


async def build_plan(session: AsyncSession) -> tuple[Product | None, list[PlanRow]]:
    """Pure, read-only. Resolves the canonical product by its Shopify
    product id, then every real `ProductVariant` row under it. Never
    writes anything.
    """
    product = (
        await session.execute(
            select(Product).where(Product.shopify_product_id == CANONICAL_SHOPIFY_PRODUCT_ID)
        )
    ).scalar_one_or_none()
    if product is None:
        return None, []

    variants = (
        (
            await session.execute(
                select(ProductVariant).where(ProductVariant.product_id == product.id)
            )
        )
        .scalars()
        .all()
    )

    plan: list[PlanRow] = []
    for variant in sorted(variants, key=lambda v: v.sku):
        pouch_count, parsed_from = _parse_pouch_count(variant.sku, variant.title)
        target = _PACK_SIZE_TO_BOXES.get(pouch_count) if pouch_count is not None else None
        plan.append(
            PlanRow(
                sku=variant.sku,
                title=variant.title,
                product_variant_id=variant.id,
                current_pack_size=variant.pack_size,
                target_pack_size=target,
                pouch_count=pouch_count,
                parsed_from=parsed_from,
            )
        )
    return product, plan


def has_blocking_problems(product: Product | None, plan: list[PlanRow]) -> bool:
    if product is None:
        return True
    return any(row.target_pack_size is None for row in plan)


def print_report(product: Product | None, plan: list[PlanRow]) -> None:
    print("=" * 78)
    print("pack_size backfill (canonical Aayush Wellness Herbal Masala) -- DRY-RUN REPORT")
    print("=" * 78)

    if product is None:
        print(
            f"\nCANONICAL PRODUCT NOT FOUND: shopify_product_id={CANONICAL_SHOPIFY_PRODUCT_ID}"
        )
        print("\nBLOCKED -- DO NOT APPLY -- resolve issues above")
        return

    print(f"\nProduct: {product.title!r} (shopify_product_id={product.shopify_product_id})")
    print(f"Real ProductVariant rows found: {len(plan)}")
    for row in plan:
        if row.target_pack_size is None:
            print(
                f"  - UNPARSED/UNMAPPED: sku={row.sku!r} title={row.title!r} "
                f"pouch_count={row.pouch_count} parsed_from={row.parsed_from}"
            )
            continue
        status = "already correct" if row.current_pack_size == row.target_pack_size else "TO UPDATE"
        print(
            f"  - sku={row.sku!r} pouch_count={row.pouch_count} (from {row.parsed_from}) "
            f"current_pack_size={row.current_pack_size} target_pack_size={row.target_pack_size}"
            f"  [{status}]"
        )

    mapped = [row for row in plan if row.target_pack_size is not None]
    to_update = sum(1 for row in mapped if row.current_pack_size != row.target_pack_size)
    already_correct = len(mapped) - to_update
    unmapped = len(plan) - len(mapped)
    blocked = has_blocking_problems(product, plan)

    print("\n" + "-" * 78)
    print("SUMMARY")
    print("-" * 78)
    print(f"TO UPDATE: {to_update}")
    print(f"ALREADY CORRECT (no-op): {already_correct}")
    print(f"UNPARSED/UNMAPPED: {unmapped}")
    verdict = "PASS" if not blocked else "BLOCKED"
    action = (
        "safe to review for --apply"
        if verdict == "PASS"
        else "DO NOT APPLY -- resolve issues above"
    )
    print(f"\n{verdict} -- {action}")


async def apply_plan(session: AsyncSession, plan: list[PlanRow]) -> None:
    service = InventoryService(session)
    updated = 0
    for row in plan:
        if row.target_pack_size is None:
            continue  # has_blocking_problems() already refused to reach here
        if row.current_pack_size == row.target_pack_size:
            continue  # idempotent no-op
        await service.update_pack_size(
            row.product_variant_id, pack_size=row.target_pack_size, actor=None
        )
        updated += 1
    print(f"\nAPPLIED: updated pack_size on {updated} ProductVariant row(s).")


async def _run(*, apply: bool) -> None:
    async with AsyncSessionLocal() as session:
        product, plan = await build_plan(session)
        print_report(product, plan)

        if not apply:
            print("\n--dry-run (default): no changes made. Re-run with --apply only after review.")
            return

        if has_blocking_problems(product, plan):
            print(
                "\nABORTING --apply: BLOCKED above (canonical product not found, or a "
                "variant's pouch count could not be parsed/mapped). No changes were made."
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
        help="Actually write the pack_size values. Default is dry-run.",
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

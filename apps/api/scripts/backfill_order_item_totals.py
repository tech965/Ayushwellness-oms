"""One-off backfill for the Shopify order-item total_amount bug.

Every `OrderItem` synced from Shopify before the `_normalize_line_item`
fix (see `app/integrations/shopify/normalizer.py`) was written with
`total_amount` and `discount_amount` computed from the wrong formula:

    old_discount_amount = discountedTotalSet_amount           (call it D)
    old_total_amount    = unit_price*quantity - D

`discountedTotalSet` is actually the line's *after-discount total*, so
the correct values are:

    correct_total_amount    = D                     == old_discount_amount
    correct_discount_amount = unit_price*qty - D     == old_total_amount

i.e. the two columns are an exact swap of each other — no re-fetch from
Shopify is needed, and no arithmetic drift is possible, since `D` is the
real historical value Shopify already returned and it's still sitting in
the (mislabeled) `discount_amount` column. `discount_amount` is clamped
at 0 for the rare case a line's discounted total exceeds unit_price*qty
(matches the clamp in the fixed normalizer).

Idempotent by construction only if run once — running it twice would
swap the columns back to the wrong values. It prints the number of rows
it changes so a re-run against an already-fixed database is obviously a
no-op to check (it swaps unconditionally, so re-running WILL undo the
fix — don't).

Run with: python scripts/backfill_order_item_totals.py
"""

from __future__ import annotations

import asyncio
from decimal import Decimal

from app.core.logging import configure_logging, get_logger
from app.db.session import AsyncSessionLocal
from app.models.mixins import SourceSystem
from app.models.order import OrderItem
from sqlalchemy import select

configure_logging()
logger = get_logger(__name__)


async def backfill() -> None:
    async with AsyncSessionLocal() as session:
        stmt = select(OrderItem).where(OrderItem.source_system == SourceSystem.SHOPIFY)
        result = await session.execute(stmt)
        items = list(result.scalars().all())

        changed = 0
        for item in items:
            old_total = item.total_amount
            old_discount = item.discount_amount
            new_total = old_discount
            new_discount = max(Decimal("0"), old_total)

            if new_total == old_total and new_discount == old_discount:
                continue

            item.total_amount = new_total
            item.discount_amount = new_discount
            changed += 1

        await session.commit()
        logger.info(
            "order_item_totals_backfilled",
            examined=len(items),
            changed=changed,
        )
        print(f"Examined {len(items)} Shopify-sourced order items, corrected {changed}.")


if __name__ == "__main__":
    asyncio.run(backfill())

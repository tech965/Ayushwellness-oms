"""TEMPORARY diagnostic script — NOT part of the app.

Pulls today's (IST) real orders directly from Shopify via the REAL
`ShopifyAdapter`/`ShopifyOrderNormalizer`/`OrderService.upsert_synced_order`
code paths (the same code the production sync uses), logs every page,
and upserts into the local dev DB — so the Shopify-vs-OMS order-count
reconciliation can be traced end-to-end against real data without
touching the production database.

Run with: python scripts/qa_shopify_reconcile.py
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from app.db.session import AsyncSessionLocal, run_with_cleanup
from app.integrations.shopify.adapter import ShopifyAdapter
from app.services.order_service import OrderService

IST = timezone(timedelta(hours=5, minutes=30))


async def main() -> None:
    today = datetime.now(IST).date()
    start = datetime(today.year, today.month, today.day, 0, 0, 0, tzinfo=IST)
    end = datetime(today.year, today.month, today.day, 23, 59, 59, tzinfo=IST)
    query_filter = f'created_at:>="{start.isoformat()}" AND created_at:<="{end.isoformat()}"'
    print(f"Business date (IST): {today}")
    print(f"Shopify query filter: {query_filter}")

    adapter = ShopifyAdapter()
    cursor: str | None = None
    page_num = 0
    all_raw: list[dict] = []

    while True:
        page_num += 1
        page = await adapter._fetch_page(  # noqa: SLF001 - reusing the exact production paging call
            "orders", cursor=cursor, limit=50, query_filter=query_filter
        )
        print(f"Page {page_num}: fetched {len(page.nodes)} orders, hasNextPage={page.has_more}")
        for node in page.nodes:
            print(
                "   ",
                node.get("name"),
                node.get("createdAt"),
                node.get("displayFinancialStatus"),
                "cancelled" if node.get("cancelledAt") else "active",
            )
        all_raw.extend(page.nodes)
        if not page.has_more:
            break
        cursor = page.next_cursor

    print(f"\nTOTAL RAW ORDERS FETCHED FROM SHOPIFY (paginated, today IST): {len(all_raw)}")

    names = [n.get("name") for n in all_raw]
    duplicates = {n for n in names if names.count(n) > 1}
    print(f"Duplicate order names within the fetched set: {duplicates or 'none'}")

    async with AsyncSessionLocal() as session:
        service = OrderService(session)
        created = 0
        updated = 0
        failed: list[tuple[str | None, str]] = []
        for raw in all_raw:
            try:
                normalized = adapter.normalize("orders", raw)
                _, was_created = await service.upsert_synced_order(**normalized)
                if was_created:
                    created += 1
                else:
                    updated += 1
            except Exception as exc:  # noqa: BLE001 - diagnostic script, log and keep going
                failed.append((raw.get("name"), f"{type(exc).__name__}: {exc}"))

        print(f"\nUPSERT RESULT: created={created} updated={updated} failed={len(failed)}")
        for name, err in failed:
            print("  FAILED:", name, "->", err)

    await adapter.aclose()


if __name__ == "__main__":
    asyncio.run(run_with_cleanup(main()))

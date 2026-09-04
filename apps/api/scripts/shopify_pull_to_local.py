"""One-shot: pull real Shopify customers/products/orders into a LOCAL DB
so the AI assistant can be smoke-tested against real commerce data
without standing up Postgres/Redis/Celery.

Reuses the OMS's own Shopify adapter + normalizers + upsert handlers
(`app.integrations`), so the rows it writes are identical to what a
normal `SyncService` run would produce. Logistics data (shipments / NDR /
RTO / couriers) is NOT pulled — that comes from Shiprocket, not Shopify.

Usage (PowerShell), from apps/api:
    $env:SHOPIFY_STORE_DOMAIN = "your-store.myshopify.com"
    $env:SHOPIFY_ACCESS_TOKEN = "shpat_..."          # or put both in .env
    $env:DATABASE_URL = "sqlite+aiosqlite:///./chat_realdata.db"
    .venv\\Scripts\\python.exe scripts\\shopify_pull_to_local.py
    .venv\\Scripts\\python.exe scripts\\shopify_pull_to_local.py --orders 300 --reset
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import UTC, datetime, timedelta

import app.models  # noqa: F401  (populate Base.metadata)
from app.core.config import settings
from app.db.base import Base
from app.db.session import AsyncSessionLocal, engine
from app.integrations.entity_sync import ENTITY_UPSERT_HANDLERS
from app.integrations.shopify.adapter import ShopifyAdapter
from app.integrations.shopify.config import ShopifyConfig

# customers/products first: orders reference them by external id.
ENTITY_ORDER = ("customers", "products", "orders")


async def _pull(
    adapter: ShopifyAdapter, entity: str, cap: int, since: datetime | None = None
) -> tuple[int, int]:
    ok = failed = 0
    cursor: str | None = None
    handler = ENTITY_UPSERT_HANDLERS[entity]
    while ok + failed < cap:
        limit = min(50, cap - ok - failed)
        if since is not None:
            page = await adapter.fetch_incremental(entity, since=since, cursor=cursor, limit=limit)
        else:
            page = await adapter.fetch(entity, cursor=cursor, limit=limit)
        for raw in page.nodes:
            async with AsyncSessionLocal() as session:
                try:
                    normalized = adapter.normalize(entity, raw)
                    await handler(session, normalized)
                    ok += 1
                except Exception as exc:  # noqa: BLE001
                    await session.rollback()
                    failed += 1
                    print(
                        f"    ! {entity} record failed: {type(exc).__name__}: {exc}",
                        file=sys.stderr,
                    )
        print(f"  {entity}: {ok} ok, {failed} failed (page done, more={page.has_more})")
        if not page.has_more or page.next_cursor is None:
            break
        cursor = page.next_cursor
    return ok, failed


async def _run(caps: dict[str, int], reset: bool, since_days: int | None) -> int:
    config = ShopifyConfig.from_settings()
    if config is None:
        print(
            "Shopify is not configured. Set SHOPIFY_STORE_DOMAIN and "
            "SHOPIFY_ACCESS_TOKEN (in .env or the environment).",
            file=sys.stderr,
        )
        return 2

    print(f"store:   {config.shop_domain}")
    print(f"api ver: {config.api_version}")
    print(f"db:      {settings.DATABASE_URL}\n")

    async with engine.begin() as conn:
        if reset:
            await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    since = datetime.now(UTC) - timedelta(days=since_days) if since_days is not None else None
    if since is not None:
        print(f"window:  orders updated since {since.date().isoformat()} (newest slice)\n")

    adapter = ShopifyAdapter()
    try:
        await adapter.authenticate()
        print("Shopify auth OK\n")
        totals: dict[str, tuple[int, int]] = {}
        for entity in ENTITY_ORDER:
            # customers/products still pull head pages; only orders honour --since-days.
            entity_since = since if entity == "orders" else None
            print(f"pulling {entity} (cap {caps[entity]})…")
            totals[entity] = await _pull(adapter, entity, caps[entity], entity_since)
        print("\nsummary:")
        for entity, (ok, failed) in totals.items():
            print(f"  {entity:<10} {ok} imported, {failed} failed")
    finally:
        await adapter.aclose()
        await engine.dispose()
    return 0


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--customers", type=int, default=500)
    p.add_argument("--products", type=int, default=300)
    p.add_argument("--orders", type=int, default=400)
    p.add_argument(
        "--since-days",
        type=int,
        default=None,
        help="Pull orders UPDATED in the last N days (newest slice) instead of the oldest page.",
    )
    p.add_argument("--reset", action="store_true", help="Drop & recreate all tables first.")
    a = p.parse_args()
    caps = {"customers": a.customers, "products": a.products, "orders": a.orders}
    raise SystemExit(asyncio.run(_run(caps, a.reset, a.since_days)))


if __name__ == "__main__":
    main()

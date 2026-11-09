"""Live (API-first) Shopify reads -- no PostgreSQL access.

Used when `settings.SHOPIFY_LIVE_MODE` is on: Shopify stays the source of
truth and its records are fetched per request, normalized in memory, and
returned to the caller. Nothing here imports a session, repository or
model that writes, and the raw GraphQL node is dropped from the result
(`raw_external_payload` is stripped), so no provider payload can reach a
persistent store through this path.

Pagination is cursor-based (Shopify `endCursor`), so a caller pages with
the opaque `next_cursor` this returns -- not page numbers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.core.exceptions import IntegrationError
from app.integrations.shopify.adapter import ShopifyAdapter

# Keys the normalizer emits that carry a copy of the provider payload.
_PAYLOAD_KEYS = ("raw_external_payload",)

# Bounds one request's crawl so a wide date range can't run unbounded
# against Shopify's cost-based rate limit.
MAX_PAGES_PER_REQUEST = 20
MAX_PAGE_SIZE = 100


@dataclass
class LivePage:
    items: list[dict[str, Any]]
    next_cursor: str | None
    has_more: bool
    # True when a bounded crawl stopped before exhausting the result set,
    # so callers can show "incomplete" rather than a misleading total.
    incomplete: bool = False
    warnings: list[str] = field(default_factory=list)


def build_orders_search(
    *,
    q: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    updated_since: datetime | None = None,
    fulfillment_status: str | None = None,
) -> str | None:
    """Shopify search-syntax string for the GraphQL `orders(query:)` arg.

    Values are quoted and stripped of quotes/backslashes so user input
    cannot inject extra search terms.
    """
    terms: list[str] = []
    if q:
        cleaned = q.strip().replace("\\", " ").replace('"', " ").strip()
        if cleaned:
            terms.append(f'"{cleaned}"' if " " in cleaned else cleaned)
    if date_from:
        terms.append(f"created_at:>='{date_from.isoformat()}'")
    if date_to:
        terms.append(f"created_at:<='{date_to.isoformat()}'")
    if updated_since:
        terms.append(f"updated_at:>'{updated_since.isoformat()}'")
    if fulfillment_status and fulfillment_status.isalpha():
        terms.append(f"fulfillment_status:{fulfillment_status.lower()}")
    return " ".join(terms) or None


def _strip_payload(normalized: dict[str, Any], raw: dict[str, Any] | None = None) -> dict[str, Any]:
    cleaned = {k: v for k, v in normalized.items() if k not in _PAYLOAD_KEYS}
    if raw is not None:
        # Display-only contact detail the sync normalizer does not carry.
        cleaned["customer_email"] = raw.get("email")
    return cleaned


class ShopifyLiveReader:
    def __init__(self, adapter: ShopifyAdapter) -> None:
        self._adapter = adapter

    async def list_orders(
        self,
        *,
        limit: int = 50,
        cursor: str | None = None,
        q: str | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        fulfillment_status: str | None = None,
    ) -> LivePage:
        """One page of live orders, normalized (OMS enums/fields), newest
        activity first (Shopify `sortKey: UPDATED_AT`)."""
        limit = max(1, min(limit, MAX_PAGE_SIZE))
        page = await self._adapter._fetch_page(  # noqa: SLF001 - same package
            "orders",
            cursor=cursor,
            limit=limit,
            query_filter=build_orders_search(
                q=q,
                date_from=date_from,
                date_to=date_to,
                fulfillment_status=fulfillment_status,
            ),
        )
        items = [
            _strip_payload(self._adapter.normalize("orders", node), node) for node in page.nodes
        ]
        return LivePage(items=items, next_cursor=page.next_cursor, has_more=page.has_more)

    async def get_orders_by_ids(
        self,
        external_ids: list[str],
        *,
        q: str | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch specific orders by Shopify numeric id (batched, bounded).

        Uses Shopify's `id:<n>` search term joined with OR. NOTE: this
        query syntax is not exercised against a real store in this
        repo's tests (mocked client only).
        """
        found: list[dict[str, Any]] = []
        clean = [i for i in external_ids if i.isdigit()]
        for start in range(0, len(clean), MAX_PAGE_SIZE):
            batch = clean[start : start + MAX_PAGE_SIZE]
            id_filter = "(" + " OR ".join(f"id:{i}" for i in batch) + ")"
            extra = build_orders_search(q=q, date_from=date_from, date_to=date_to)
            query_filter = f"{id_filter} {extra}" if extra else id_filter
            page = await self._adapter._fetch_page(  # noqa: SLF001 - same package
                "orders", cursor=None, limit=len(batch), query_filter=query_filter
            )
            found.extend(
                _strip_payload(self._adapter.normalize("orders", node), node) for node in page.nodes
            )
        return found

    async def get_order(self, external_id: str) -> dict[str, Any] | None:
        orders = await self.get_orders_by_ids([external_id])
        return orders[0] if orders else None

    async def iter_orders(
        self,
        *,
        q: str | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        max_pages: int = MAX_PAGES_PER_REQUEST,
    ) -> LivePage:
        """Bounded multi-page crawl for aggregation/export. Flags the
        result `incomplete` if `max_pages` was hit with more remaining,
        so a truncated total is never presented as the real total."""
        items: list[dict[str, Any]] = []
        cursor: str | None = None
        has_more = False
        for _ in range(max_pages):
            page = await self.list_orders(
                limit=MAX_PAGE_SIZE, cursor=cursor, q=q, date_from=date_from, date_to=date_to
            )
            items.extend(page.items)
            cursor, has_more = page.next_cursor, page.has_more
            if not has_more:
                break
        result = LivePage(items=items, next_cursor=cursor, has_more=has_more, incomplete=has_more)
        if has_more:
            result.warnings.append(
                f"Stopped after {max_pages} pages; results are incomplete. Narrow the date range."
            )
        return result


def require_live_reader(adapter: Any) -> ShopifyLiveReader:
    if not isinstance(adapter, ShopifyAdapter):
        raise IntegrationError(
            "Shopify integration is not configured.",
            details={"error_type": "not_configured"},
        )
    return ShopifyLiveReader(adapter)

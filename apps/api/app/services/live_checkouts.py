"""Live (API-first) abandoned checkouts.

Shopify is the source of truth for a checkout's content (customer contact,
items, totals, recovery link, completion). It is fetched per request and
turned into a transient `AbandonedCheckout` (never flushed -- see
`app.services.live_orders` for the guard); only `checkout_workflow` (a
reference) and the telecalling records hang off it in PostgreSQL.

NOTE: the `abandonedCheckouts` GraphQL root field and its search syntax are
used exactly as the existing (never live-verified) query documents them;
this repo's tests mock them. See the report's real-API verification list.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from io import BytesIO
from typing import Any

from openpyxl import Workbook
from openpyxl.utils import get_column_letter
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import IntegrationError
from app.integrations.registry import get_adapter
from app.integrations.shopify.adapter import ShopifyAdapter
from app.integrations.shopify.errors import ShopifyApiError
from app.integrations.shopify.live import MAX_PAGE_SIZE, build_orders_search
from app.integrations.shopify.queries import ABANDONED_CHECKOUTS_QUERY
from app.models.abandoned_checkout import AbandonedCheckout
from app.models.checkout_workflow import CheckoutWorkflow
from app.models.integration import IntegrationCode
from app.services.order_ref import checkout_ref_id_for, get_checkout_workflows_by_external_ids

MAX_CHECKOUT_PAGES = 20
_PAYLOAD_KEYS = ("raw_external_payload",)


def _adapter() -> ShopifyAdapter:
    adapter = get_adapter(IntegrationCode.SHOPIFY)
    if not isinstance(adapter, ShopifyAdapter):
        raise IntegrationError(
            "Shopify integration is not configured.", details={"error_type": "not_configured"}
        )
    return adapter


async def fetch_checkouts(
    *,
    cursor: str | None = None,
    limit: int = MAX_PAGE_SIZE,
    query_filter: str | None = None,
) -> tuple[list[dict[str, Any]], str | None, bool]:
    """One page of normalized live checkouts: (items, next_cursor, has_more)."""
    adapter = _adapter()
    client = adapter._get_client()  # noqa: SLF001 - same integration package family
    try:
        data = await client.execute(
            ABANDONED_CHECKOUTS_QUERY,
            {"first": max(1, min(limit, MAX_PAGE_SIZE)), "after": cursor, "query": query_filter},
        )
    except ShopifyApiError as exc:
        raise IntegrationError(exc.message, details={"error_type": exc.error_type}) from exc
    connection = data.get("abandonedCheckouts") or {}
    page_info = connection.get("pageInfo") or {}
    items = []
    for edge in connection.get("edges") or []:
        normalized = adapter.normalize("abandoned_checkouts", edge["node"])
        items.append({k: v for k, v in normalized.items() if k not in _PAYLOAD_KEYS})
    has_more = bool(page_info.get("hasNextPage"))
    return items, (page_info.get("endCursor") if has_more else None), has_more


async def fetch_checkouts_by_ids(external_ids: list[str]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    clean = [i for i in external_ids if i.isdigit()]
    for start in range(0, len(clean), MAX_PAGE_SIZE):
        batch = clean[start : start + MAX_PAGE_SIZE]
        items, _, _ = await fetch_checkouts(
            limit=len(batch), query_filter="(" + " OR ".join(f"id:{i}" for i in batch) + ")"
        )
        found.extend(items)
    return found


def hydrate_checkout(data: dict[str, Any], workflow: CheckoutWorkflow | None) -> AbandonedCheckout:
    external_id = data["external_id"]
    created = data.get("checkout_created_at") or data.get("external_created_at")
    updated = data.get("checkout_updated_at") or created
    checkout = AbandonedCheckout(
        id=checkout_ref_id_for(external_id, workflow),
        shopify_checkout_id=external_id,
        customer_id=None,
        customer_name=data.get("customer_name"),
        customer_phone=data.get("customer_phone"),
        customer_email=data.get("customer_email"),
        checkout_url=data.get("checkout_url"),
        currency=data.get("currency") or "INR",
        subtotal_amount=data.get("subtotal_amount") or Decimal("0"),
        total_amount=data.get("total_amount") or Decimal("0"),
        line_items=data.get("line_items"),
        is_recovered=bool(data.get("is_recovered")),
        checkout_created_at=created,
        checkout_updated_at=updated,
        source_system="shopify",
        external_id=external_id,
        created_at=created,
        updated_at=updated,
    )
    checkout.customer = None
    checkout._live_transient = True  # type: ignore[attr-defined]  # noqa: SLF001
    checkout._live_external_id = external_id  # type: ignore[attr-defined]  # noqa: SLF001
    return checkout


def is_contactable(data: dict[str, Any]) -> bool:
    """Spec: never turn anonymous cart activity into a telecalling lead."""
    return bool(data.get("customer_phone") or data.get("customer_email"))


def checkout_search(
    *, date_from: datetime | None = None, date_to: datetime | None = None
) -> str | None:
    return build_orders_search(date_from=date_from, date_to=date_to)


async def collect_checkouts(
    session: AsyncSession,
    *,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    only_open: bool = True,
    only_contactable: bool = True,
    max_pages: int = MAX_CHECKOUT_PAGES,
) -> tuple[list[dict[str, Any]], dict[str, CheckoutWorkflow], bool]:
    """Bounded crawl. Returns (checkouts, workflows, incomplete)."""
    matched: list[dict[str, Any]] = []
    workflows: dict[str, CheckoutWorkflow] = {}
    cursor: str | None = None
    has_more = True
    pages = 0
    query = checkout_search(date_from=date_from, date_to=date_to)
    while has_more and pages < max_pages:
        items, cursor, has_more = await fetch_checkouts(cursor=cursor, query_filter=query)
        pages += 1
        workflows.update(
            await get_checkout_workflows_by_external_ids(session, [i["external_id"] for i in items])
        )
        for item in items:
            if only_open and item.get("is_recovered"):
                continue
            if only_contactable and not is_contactable(item):
                continue
            matched.append(item)
    return matched, workflows, has_more


CHECKOUT_EXPORT_HEADERS = [
    "Checkout ID",
    "Customer",
    "Phone",
    "Email",
    "Items",
    "Total",
    "Currency",
    "Created",
    "Recovered",
    "Assigned To",
    "Call Status",
    "Attempts",
    "Next Follow-up",
]


def checkouts_to_xlsx(rows: list[tuple[AbandonedCheckout, Any]]) -> bytes:
    """(checkout, assignment|None) pairs -> workbook (pure formatting)."""
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Abandoned checkouts"
    sheet.append(CHECKOUT_EXPORT_HEADERS)

    def naive(value: datetime | None) -> datetime | None:
        return value.replace(tzinfo=None) if value is not None else None

    for checkout, assignment in rows:
        items = "; ".join(
            f"{i.get('title')} x{i.get('quantity')}" for i in (checkout.line_items or [])
        )
        sheet.append(
            [
                checkout.shopify_checkout_id,
                checkout.customer_name,
                checkout.customer_phone,
                checkout.customer_email,
                items,
                float(checkout.total_amount),
                checkout.currency,
                naive(checkout.checkout_created_at),
                "yes" if checkout.is_recovered else "no",
                assignment.telecaller.name if assignment is not None else None,
                assignment.current_status.value if assignment is not None else None,
                assignment.attempt_count if assignment is not None else None,
                naive(assignment.next_follow_up_at) if assignment is not None else None,
            ]
        )
    for index in range(1, len(CHECKOUT_EXPORT_HEADERS) + 1):
        sheet.column_dimensions[get_column_letter(index)].width = 22
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()

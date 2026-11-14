"""Flipkart Seller API order sync -- live from the Search Shipments API,
nothing persisted. See `app.integrations.flipkart.orders` for the exact
documented endpoint/field mapping this is built on.
"""

from __future__ import annotations

import time
from datetime import UTC, date, datetime
from decimal import Decimal

from app.integrations.flipkart.client import FlipkartClient
from app.integrations.flipkart.config import FlipkartConfig
from app.integrations.flipkart.errors import FlipkartNotConfiguredError
from app.integrations.flipkart.orders import (
    POST_DISPATCH_STATES,
    PRE_DISPATCH_STATES,
    FlipkartOrders,
)
from app.schemas.flipkart_sales import FlipkartOrderItem, FlipkartOrdersPage, FlipkartStatus

# Same reasoning as Amazon's ORDERS_API_TIME_BUDGET_SECONDS: this backend
# may run as a Vercel serverless function with a 60s hard limit (see
# apps/api/vercel.json); a full-export page walk must stop itself well
# before that rather than being killed with no response.
EXPORT_TIME_BUDGET_SECONDS = 45.0

_last_success: datetime | None = None  # operational metadata, not provider data


def last_successful_fetch() -> datetime | None:
    return _last_success


def _mark_success() -> datetime:
    global _last_success
    _last_success = datetime.now(UTC)
    return _last_success


def dispatch_type_for_status(status: str | None) -> tuple[str, list[str]]:
    """Maps a single OMS-facing status filter onto Flipkart's own
    `filter.type` + `filter.states` shape (OMAPIRef.html). `status=None`
    defaults to `preDispatch` -- Flipkart requires picking one dispatch
    type per call, there's no "all" option documented.
    """
    if status is None or status in PRE_DISPATCH_STATES:
        states = [status] if status else list(PRE_DISPATCH_STATES)
        return "preDispatch", states
    if status in POST_DISPATCH_STATES:
        return "postDispatch", [status]
    if status == "CANCELLED":
        return "cancelled", [status]
    known = [*PRE_DISPATCH_STATES, *POST_DISPATCH_STATES, "CANCELLED"]
    raise ValueError(f"Unknown status '{status}'. Known: {known}")


def _parse_page(data: dict) -> tuple[list[FlipkartOrderItem], str | None, bool]:
    items: list[FlipkartOrderItem] = []
    for shipment in data.get("shipments", []):
        shipment_id = shipment.get("shipmentId", "")
        package_title = None
        for sub in shipment.get("subShipments", []):
            if sub.get("packageTitle"):
                package_title = sub["packageTitle"]
                break
        for oi in shipment.get("orderItems", []):
            price = oi.get("priceComponents", {}).get("sellingPrice")
            items.append(
                FlipkartOrderItem(
                    order_id=oi.get("orderId", ""),
                    shipment_id=shipment_id,
                    order_date=oi.get("orderDate", ""),
                    status=oi.get("status", "UNKNOWN"),
                    product_name=package_title,
                    sku=oi.get("sku"),
                    fsn=oi.get("fsn"),
                    quantity=int(oi.get("quantity", 0)),
                    selling_price=Decimal(str(price)) if price is not None else None,
                )
            )
    return items, data.get("nextPageUrl"), bool(data.get("hasMore"))


class FlipkartSalesService:
    def __init__(self, config: FlipkartConfig | None = None, client: FlipkartClient | None = None):
        self.config = config or FlipkartConfig.from_settings()
        self._client = client
        self._orders: FlipkartOrders | None = None

    def status(self) -> FlipkartStatus:
        c = self.config
        return FlipkartStatus(
            configured=c.configured,
            missing=c.missing(),
            last_successful_fetch_at=last_successful_fetch(),
        )

    def _api(self) -> FlipkartOrders:
        if not self.config.configured:
            raise FlipkartNotConfiguredError(
                "Flipkart Seller API credentials are not configured.",
                details={"missing": self.config.missing()},
            )
        if self._orders is None:
            self._client = self._client or FlipkartClient(self.config)
            self._orders = FlipkartOrders(self._client)
        return self._orders

    async def orders_page(
        self,
        *,
        date_from: date | None,
        date_to: date | None,
        status: str | None,
        page_size: int,
        next_page_url: str | None,
    ) -> FlipkartOrdersPage:
        api = self._api()
        dispatch_type, states = dispatch_type_for_status(status)
        data = await api.search_shipments(
            dispatch_type=dispatch_type,
            states=states,
            order_date_from=f"{date_from.isoformat()}T00:00:00" if date_from else None,
            order_date_to=f"{date_to.isoformat()}T23:59:59" if date_to else None,
            page_size=page_size,
            next_page_url=next_page_url,
        )
        items, next_url, has_more = _parse_page(data)
        _mark_success()
        return FlipkartOrdersPage(
            date_from=date_from,
            date_to=date_to,
            fetched_at=datetime.now(UTC),
            items=items,
            next_page_url=next_url,
            has_more=has_more,
            unavailable={
                "payment_type": "Not present in Flipkart's Search Shipments API response "
                "(verified against the official Order Management API reference); no other "
                "documented field provides it on this endpoint.",
                "tracking_id": "Only available from the separate GET /v3/shipments/{id} "
                "detail lookup, not the Search Shipments API used for listing.",
            },
            warnings=[
                "Flipkart Advantage (FA) orders are not included -- the Search Shipments API "
                "documents that it only returns orders fulfilled via the seller's own "
                "fulfillment service."
            ],
        )

    async def all_orders_for_export(
        self, *, date_from: date | None, date_to: date | None, status: str | None
    ) -> FlipkartOrdersPage:
        """Walks every page (`nextPageUrl`) for the export, not just the
        first 20 -- bounded by a wall-clock budget so this can't run past
        the backend's own request time limit. Partial results are always
        reported via `warnings`/`has_more`, never silently dropped.
        """
        first = await self.orders_page(
            date_from=date_from,
            date_to=date_to,
            status=status,
            page_size=20,
            next_page_url=None,
        )
        items = list(first.items)
        next_url = first.next_page_url
        has_more = first.has_more
        warnings = list(first.warnings)
        started_at = time.monotonic()
        while has_more and next_url:
            if time.monotonic() - started_at > EXPORT_TIME_BUDGET_SECONDS:
                warnings.append(
                    f"Export stopped after {len(items)} orders "
                    f"({EXPORT_TIME_BUDGET_SECONDS:.0f}s time budget reached); narrow the date "
                    "range or status filter for a complete export."
                )
                break
            page = await self.orders_page(
                date_from=date_from,
                date_to=date_to,
                status=status,
                page_size=20,
                next_page_url=next_url,
            )
            items.extend(page.items)
            next_url = page.next_page_url
            has_more = page.has_more
        return FlipkartOrdersPage(
            date_from=date_from,
            date_to=date_to,
            fetched_at=first.fetched_at,
            items=items,
            next_page_url=next_url,
            has_more=has_more,
            unavailable=first.unavailable,
            warnings=warnings,
        )

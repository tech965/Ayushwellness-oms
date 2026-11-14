"""Flipkart Order Management API operations used for the read-only order
sync view. Endpoints and field names verified against
https://seller.flipkart.com/api-docs/order-api-docs/OMAPIRef.html
(Order Management API reference, "Developer API v3.0") -- see that
page's "Search Shipments API" (`POST /v3/shipments/filter/`) section for
the request/response shapes used here. Nothing here is persisted.
"""

from __future__ import annotations

from typing import Any

from app.integrations.flipkart.client import FlipkartClient

# Documented preDispatch/postDispatch/cancelled states
# (OMAPIRef.html, "Search Shipments API" -> filter.states).
PRE_DISPATCH_STATES = (
    "APPROVED",
    "PACKING_IN_PROGRESS",
    "PACKED",
    "FORM_FAILED",
    "READY_TO_DISPATCH",
)
POST_DISPATCH_STATES = ("SHIPPED", "DELIVERED", "PICKUP_COMPLETE")
# Documented maximum (OMAPIRef.html: "Maximum size is 20").
MAX_PAGE_SIZE = 20


class FlipkartOrders:
    def __init__(self, client: FlipkartClient) -> None:
        self.c = client

    async def search_shipments(
        self,
        *,
        dispatch_type: str,
        states: list[str],
        order_date_from: str | None = None,
        order_date_to: str | None = None,
        page_size: int = MAX_PAGE_SIZE,
        next_page_url: str | None = None,
    ) -> dict[str, Any]:
        """One page of the Search Shipments API. `next_page_url`, when
        given, is Flipkart's own `nextPageUrl` from a prior response --
        callers pass it straight through rather than reconstructing
        filters, matching how the documented pagination works.
        """
        if next_page_url:
            return await self.c.request("GET", next_page_url)

        body: dict[str, Any] = {
            "filter": {"type": dispatch_type, "states": states},
            "pagination": {"pageSize": min(page_size, MAX_PAGE_SIZE)},
        }
        if order_date_from or order_date_to:
            body["filter"]["orderDate"] = {}
            if order_date_from:
                body["filter"]["orderDate"]["from"] = order_date_from
            if order_date_to:
                body["filter"]["orderDate"]["to"] = order_date_to
        return await self.c.request("POST", "/sellers/v3/shipments/filter/", json=body)

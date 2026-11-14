"""SP-API Reports + Sales + Catalog operations used for sales analytics."""

from __future__ import annotations

import json
import time
from datetime import date, timedelta

from app.integrations.amazon.client import AmazonClient
from app.integrations.amazon.errors import AmazonRateLimitError, AmazonReportError

REPORT_TYPE = "GET_SALES_AND_TRAFFIC_REPORT"
# Amazon: this report "can be requested up to three times every five minutes".
REPORT_REQUESTS_PER_WINDOW = 3
REPORT_WINDOW_SECONDS = 300.0


class AmazonReports:
    # monotonic timestamps of recent createReport calls (process-wide)
    recent_creates: list[float] = []

    @classmethod
    def report_capacity(cls) -> int:
        now = time.monotonic()
        cls.recent_creates[:] = [t for t in cls.recent_creates if now - t < REPORT_WINDOW_SECONDS]
        return max(REPORT_REQUESTS_PER_WINDOW - len(cls.recent_creates), 0)

    def __init__(
        self,
        client: AmazonClient,
        marketplace_id: str,
        tz_offset: str,
        timezone: str = "Asia/Kolkata",
    ) -> None:
        self.timezone = timezone
        self.c = client
        self.marketplace_id = marketplace_id
        self.tz_offset = tz_offset

    async def create_report(self, start: date, end: date) -> str:
        if self.report_capacity() < 1:
            raise AmazonRateLimitError(
                "Amazon allows only 3 Sales & Traffic report requests every 5 minutes; "
                "please retry shortly."
            )
        self.recent_creates.append(time.monotonic())
        body = {
            "reportType": REPORT_TYPE,
            "marketplaceIds": [self.marketplace_id],
            "dataStartTime": f"{start.isoformat()}T00:00:00{self.tz_offset}",
            "dataEndTime": f"{end.isoformat()}T23:59:59{self.tz_offset}",
            "reportOptions": {"dateGranularity": "DAY", "asinGranularity": "SKU"},
        }
        data = await self.c.request(
            "createReport", "POST", "/reports/2021-06-30/reports", json=body
        )
        return str(data["reportId"])

    async def report_status(self, report_id: str) -> dict:
        return await self.c.request("getReport", "GET", f"/reports/2021-06-30/reports/{report_id}")

    async def fetch_document(self, report_document_id: str) -> dict:
        doc = await self.c.request(
            "getReportDocument", "GET", f"/reports/2021-06-30/documents/{report_document_id}"
        )
        raw = await self.c.download_document(doc["url"], doc.get("compressionAlgorithm"))
        try:
            return json.loads(raw.decode("utf-8"))
        except ValueError as exc:
            raise AmazonReportError("Amazon report was not valid JSON.") from exc

    async def order_metrics(
        self,
        start: date,
        end: date,
        *,
        granularity: str = "Day",
        sku: str | None = None,
        asin: str | None = None,
    ) -> list[dict]:
        params = {
            "marketplaceIds": self.marketplace_id,
            # interval end is exclusive per Amazon -> midnight after the last day
            "interval": (
                f"{start.isoformat()}T00:00:00{self.tz_offset}--"
                f"{(end + timedelta(days=1)).isoformat()}T00:00:00{self.tz_offset}"
            ),
            "granularity": granularity,
            # must be an IANA zone matching the interval offset
            "granularityTimeZone": self.timezone,
        }
        if sku:
            params["sku"] = sku
        if asin:
            params["asin"] = asin
        data = await self.c.request("orderMetrics", "GET", "/sales/v1/orderMetrics", params=params)
        return data.get("payload", [])

    async def list_orders(self, start: date, end: date) -> list[dict]:
        """All orders created in [start, end], paginated. Only order-level
        fields needed for product aggregation are used by callers
        (AmazonOrderId, PurchaseDate, OrderStatus) -- never buyer/address
        fields, even though Amazon's raw response may include them.
        """
        params: dict = {
            "MarketplaceIds": self.marketplace_id,
            "CreatedAfter": f"{start.isoformat()}T00:00:00Z",
            "CreatedBefore": f"{(end + timedelta(days=1)).isoformat()}T00:00:00Z",
            "MaxResultsPerPage": 100,
        }
        orders: list[dict] = []
        next_token: str | None = None
        while True:
            p = (
                {"MarketplaceIds": self.marketplace_id, "NextToken": next_token}
                if next_token
                else params
            )
            data = await self.c.request("getOrders", "GET", "/orders/v0/orders", params=p)
            payload = data.get("payload", {})
            orders.extend(payload.get("Orders", []))
            next_token = payload.get("NextToken")
            if not next_token:
                break
        return orders

    async def order_items(self, order_id: str) -> list[dict]:
        """Line items for one order, paginated. Only ASIN/SellerSKU/Title/
        QuantityOrdered/ItemPrice are used by callers -- never PII.
        """
        items: list[dict] = []
        next_token: str | None = None
        while True:
            params = {"NextToken": next_token} if next_token else None
            data = await self.c.request(
                "getOrderItems",
                "GET",
                f"/orders/v0/orders/{order_id}/orderItems",
                params=params,
            )
            payload = data.get("payload", {})
            items.extend(payload.get("OrderItems", []))
            next_token = payload.get("NextToken")
            if not next_token:
                break
        return items

    async def catalog_titles(self, asins: list[str]) -> dict[str, str]:
        """ASIN -> title, 20 per call. Returns what Amazon gave; missing = absent."""
        out: dict[str, str] = {}
        for i in range(0, len(asins), 20):
            chunk = asins[i : i + 20]
            data = await self.c.request(
                "catalog",
                "GET",
                "/catalog/2022-04-01/items",
                params={
                    "identifiers": ",".join(chunk),
                    "identifiersType": "ASIN",
                    "marketplaceIds": self.marketplace_id,
                    "includedData": "summaries",
                },
            )
            for item in data.get("items", []):
                for s in item.get("summaries", []):
                    if s.get("itemName"):
                        out[item["asin"]] = s["itemName"]
                        break
        return out

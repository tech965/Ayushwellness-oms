"""Amazon product sales analytics -- live from SP-API, nothing persisted.

Data source: Reports API `GET_SALES_AND_TRAFFIC_REPORT` (DAY / SKU
granularity) -- one report per <=30-day window, created and polled
statelessly (the client holds the report ids; the server stores nothing).
Report documents are downloaded into memory, parsed, aggregated and
discarded. Per-product daily trend uses the Sales API (`orderMetrics`).

Revenue definitions (Amazon's, see DEFINITIONS): "ordered" sales are
reported when the order is placed and are NOT reduced by later
cancellations or refunds; "shipped" sales/units exist only at account-day
level in this report. Neither is profit.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.integrations.amazon.client import AmazonClient
from app.integrations.amazon.config import AmazonConfig
from app.integrations.amazon.errors import (
    AmazonDateRangeError,
    AmazonNotConfiguredError,
    AmazonRateLimitError,
    AmazonReportError,
)
from app.integrations.amazon.reports import AmazonReports
from app.schemas.amazon_sales import (
    AmazonStatus,
    ChunkRef,
    DailyPoint,
    PeriodPoint,
    PollResponse,
    ProductRow,
    SalesReport,
    SalesSummary,
)

CHUNK_DAYS = 30
# Amazon: dataStartTime must not be more than two years before the request.
MAX_LOOKBACK_DAYS = 729

DEFINITIONS = {
    "units_ordered": "Units in orders placed in the period (Amazon 'unitsOrdered'). Not reduced "
    "by later cancellations or returns.",
    "ordered_revenue": "Amazon 'orderedProductSales': value of products ordered in the period. "
    "Not net of cancellations/refunds; excludes tax/shipping per Amazon's definition. "
    "This is sales revenue, NOT profit.",
    "shipped_revenue": "Amazon 'shippedProductSales' (account/day level only). Not profit.",
    "units_shipped": "Amazon 'unitsShipped' (account/day level only).",
    "units_refunded": "Amazon 'unitsRefunded' (account/day level only).",
    "avg_selling_price": "ordered_revenue / units_ordered; unavailable when units_ordered is 0.",
    "products_sold": "Distinct ASINs with units_ordered > 0 in the period.",
    "product_rows": "One row per (ASIN, SKU) from the report's by-ASIN section; windows are "
    "disjoint so rows are summed without double counting.",
    "weekly": "Sum of daily values, weeks start Monday.",
    "monthly": "Sum of daily values by calendar month.",
}

_last_success: datetime | None = None  # operational metadata, not provider data


def last_successful_fetch() -> datetime | None:
    return _last_success


def _mark_success() -> datetime:
    global _last_success
    _last_success = datetime.now(UTC)
    return _last_success


def _money(node: dict | None) -> tuple[Decimal, str | None]:
    if not node:
        return Decimal(0), None
    return Decimal(str(node.get("amount", 0))), node.get("currencyCode")


def split_chunks(start: date, end: date) -> list[tuple[date, date]]:
    out = []
    cur = start
    while cur <= end:
        stop = min(cur + timedelta(days=CHUNK_DAYS - 1), end)
        out.append((cur, stop))
        cur = stop + timedelta(days=1)
    return out


def tz_offset(tzname: str) -> str:
    off = datetime.now(ZoneInfo(tzname)).utcoffset() or timedelta(0)
    mins = int(off.total_seconds() // 60)
    sign = "+" if mins >= 0 else "-"
    mins = abs(mins)
    return f"{sign}{mins // 60:02d}:{mins % 60:02d}"


def build_report(
    docs: list[dict], date_from: date, date_to: date, titles: dict[str, str]
) -> SalesReport:
    """Pure aggregation of parsed report documents (no I/O)."""
    daily: dict[date, DailyPoint] = {}
    products: dict[tuple[str, str | None], ProductRow] = {}
    currencies: set[str] = set()
    warnings: list[str] = []
    has_shipped = has_refunds = False

    for doc in docs:
        for row in doc.get("salesAndTrafficByDate", []):
            d = date.fromisoformat(row["date"][:10])
            if not (date_from <= d <= date_to):
                continue
            s = row.get("salesByDate", {})
            rev, cur = _money(s.get("orderedProductSales"))
            if cur:
                currencies.add(cur)
            shipped_rev = None
            if s.get("shippedProductSales") is not None:
                shipped_rev, c2 = _money(s["shippedProductSales"])
                if c2:
                    currencies.add(c2)
            units_shipped = s.get("unitsShipped")
            refunded = s.get("unitsRefunded")
            has_shipped = has_shipped or units_shipped is not None
            has_refunds = has_refunds or refunded is not None
            # keyed by date: a date seen twice replaces, never adds (no double count)
            daily[d] = DailyPoint(
                date=d,
                units_ordered=int(s.get("unitsOrdered", 0)),
                ordered_revenue=rev,
                order_items=s.get("totalOrderItems"),
                units_shipped=units_shipped,
                shipped_revenue=shipped_rev,
                units_refunded=refunded,
            )
        for row in doc.get("salesAndTrafficByAsin", []):
            asin = row.get("childAsin") or row.get("parentAsin")
            if not asin:
                continue
            sku = row.get("sku")
            s = row.get("salesByAsin", {})
            rev, cur = _money(s.get("orderedProductSales"))
            if cur:
                currencies.add(cur)
            units = int(s.get("unitsOrdered", 0))
            key = (asin, sku)
            prev = products.get(key)
            if prev:
                units += prev.units_ordered
                rev += prev.ordered_revenue
            products[key] = ProductRow(
                asin=asin,
                sku=sku,
                product_name=titles.get(asin),
                units_ordered=units,
                ordered_revenue=rev,
                avg_selling_price=(rev / units).quantize(Decimal("0.01")) if units else None,
            )

    if len(currencies) > 1:
        warnings.append(f"Multiple currencies in report ({sorted(currencies)}); totals mix them.")
    currency = sorted(currencies)[0] if currencies else None

    days = [daily[k] for k in sorted(daily)]
    total_units = sum(p.units_ordered for p in days)
    total_rev = sum((p.ordered_revenue for p in days), Decimal(0))
    rows = sorted(products.values(), key=lambda r: r.ordered_revenue, reverse=True)

    prod_units = sum(r.units_ordered for r in rows)
    prod_rev = sum((r.ordered_revenue for r in rows), Decimal(0))
    if rows and (prod_units != total_units or prod_rev != total_rev):
        warnings.append(
            f"Product rows total {prod_units} units / {prod_rev} vs daily total "
            f"{total_units} units / {total_rev}. Daily totals are used for headline metrics; "
            "the difference comes from Amazon's report and is not adjusted."
        )

    unavailable = {
        "product.units_shipped": "Amazon's by-ASIN sales report has no shipped units per product.",
        "product.average_selling_price": "Unavailable for products with 0 units ordered.",
    }
    if not has_shipped:
        unavailable["daily.units_shipped"] = "Not present in the Amazon report returned."
    if not has_refunds:
        unavailable["daily.units_refunded"] = "Not present in the Amazon report returned."
    if any(r.product_name is None for r in rows):
        unavailable["product.product_name"] = (
            "Titles come from the Catalog Items API; missing for some ASINs "
            "(permission or catalog gap)."
        )

    def agg(keyfn, labelfn) -> list[PeriodPoint]:
        buckets: dict[date, list[DailyPoint]] = defaultdict(list)
        for p in days:
            buckets[keyfn(p.date)].append(p)
        out = []
        for k in sorted(buckets):
            ps = buckets[k]
            sh = [p.units_shipped for p in ps]
            shr = [p.shipped_revenue for p in ps]
            rf = [p.units_refunded for p in ps]
            out.append(
                PeriodPoint(
                    period=k.isoformat(),
                    label=labelfn(k),
                    units_ordered=sum(p.units_ordered for p in ps),
                    ordered_revenue=sum((p.ordered_revenue for p in ps), Decimal(0)),
                    units_shipped=sum(sh) if all(x is not None for x in sh) else None,
                    shipped_revenue=(
                        sum(shr, Decimal(0)) if all(x is not None for x in shr) else None
                    ),
                    units_refunded=sum(rf) if all(x is not None for x in rf) else None,
                )
            )
        return out

    weekly = agg(lambda d: d - timedelta(days=d.weekday()), lambda k: f"Week of {k.isoformat()}")
    monthly = agg(lambda d: d.replace(day=1), lambda k: k.strftime("%Y-%m"))

    shipped_ok = has_shipped and all(p.units_shipped is not None for p in days)
    summary = SalesSummary(
        currency=currency,
        total_units_ordered=total_units,
        total_ordered_revenue=total_rev,
        products_sold=len({r.asin for r in rows if r.units_ordered > 0}),
        total_units_shipped=sum(p.units_shipped for p in days) if shipped_ok else None,
        total_shipped_revenue=(
            sum((p.shipped_revenue for p in days), Decimal(0))
            if shipped_ok and all(p.shipped_revenue is not None for p in days)
            else None
        ),
        total_units_refunded=(
            sum(p.units_refunded for p in days)
            if has_refunds and all(p.units_refunded is not None for p in days)
            else None
        ),
    )
    return SalesReport(
        date_from=date_from,
        date_to=date_to,
        fetched_at=datetime.now(UTC),
        summary=summary,
        daily=days,
        weekly=weekly,
        monthly=monthly,
        products=rows,
        unavailable=unavailable,
        warnings=warnings,
        definitions=DEFINITIONS,
    )


def filter_products(rows: list[ProductRow], q: str | None) -> list[ProductRow]:
    if not q or not q.strip():
        return rows
    needle = q.strip().lower()
    return [
        r
        for r in rows
        if needle in (r.sku or "").lower()
        or needle in r.asin.lower()
        or needle in (r.product_name or "").lower()
    ]


class AmazonSalesService:
    def __init__(self, config: AmazonConfig | None = None, client: AmazonClient | None = None):
        self.config = config or AmazonConfig.from_settings()
        self._client = client
        self._reports: AmazonReports | None = None

    def status(self) -> AmazonStatus:
        c = self.config
        return AmazonStatus(
            configured=c.configured,
            missing=c.missing(),
            region=c.region,
            marketplace_id=c.marketplace_id,
            max_range_days=c.max_range_days,
            last_successful_fetch_at=last_successful_fetch(),
        )

    def _api(self) -> AmazonReports:
        if not self.config.configured:
            raise AmazonNotConfiguredError(
                "Amazon SP-API credentials are not configured.",
                details={"missing": self.config.missing()},
            )
        if self._reports is None:
            self._client = self._client or AmazonClient(self.config)
            self._reports = AmazonReports(
                self._client,
                self.config.marketplace_id or "",
                tz_offset(self.config.timezone),
                self.config.timezone,
            )
        return self._reports

    def validate_range(self, start: date, end: date) -> None:
        today = datetime.now(ZoneInfo(self.config.timezone)).date()
        if start > end:
            raise AmazonDateRangeError("Start date must be on or before end date.")
        if end > today:
            raise AmazonDateRangeError("End date cannot be in the future.")
        if (today - start).days > MAX_LOOKBACK_DAYS:
            raise AmazonDateRangeError(
                "Amazon only serves report data from the last 2 years; choose a later start date."
            )
        if (end - start).days + 1 > self.config.max_range_days:
            raise AmazonDateRangeError(
                f"Date range too long; maximum is {self.config.max_range_days} days per request."
            )

    async def start_fetch(self, start: date, end: date) -> list[ChunkRef]:
        self.validate_range(start, end)
        api = self._api()
        windows = split_chunks(start, end)
        if len(windows) > api.report_capacity():
            raise AmazonRateLimitError(
                "Amazon allows only 3 Sales & Traffic report requests every 5 minutes; "
                f"this range needs {len(windows)} and {api.report_capacity()} remain. "
                "Retry in a few minutes or choose a shorter range."
            )
        chunks = []
        for s, e in windows:
            chunks.append(ChunkRef(report_id=await api.create_report(s, e), start=s, end=e))
        return chunks

    async def _documents(self, chunks: list[ChunkRef]) -> tuple[str, dict[str, str], list[dict]]:
        api = self._api()
        statuses: dict[str, str] = {}
        done: dict[str, str] = {}
        for ch in chunks:
            r = await api.report_status(ch.report_id)
            st = r.get("processingStatus", "UNKNOWN")
            statuses[ch.report_id] = st
            if st in ("CANCELLED", "FATAL"):
                hint = (
                    " (Amazon often cancels a report when there is no data for the window)"
                    if st == "CANCELLED"
                    else ""
                )
                raise AmazonReportError(
                    f"Amazon report {ch.report_id} ended as {st}{hint}.",
                    details={"chunk": ch.model_dump(mode="json")},
                )
            if st == "DONE":
                done[ch.report_id] = r.get("reportDocumentId", "")
        if len(done) < len(chunks):
            return "processing", statuses, []
        docs = [await api.fetch_document(done[ch.report_id]) for ch in chunks]
        return "done", statuses, docs

    async def poll(self, chunks: list[ChunkRef], q: str | None = None) -> PollResponse:
        state, statuses, docs = await self._documents(chunks)
        if state == "processing":
            return PollResponse(state=state, chunk_statuses=statuses)
        report = await self._assemble(chunks, docs)
        report.products = filter_products(report.products, q)
        _mark_success()
        return PollResponse(state="done", chunk_statuses=statuses, report=report)

    async def full_report(self, chunks: list[ChunkRef]) -> SalesReport:
        state, _, docs = await self._documents(chunks)
        if state != "done":
            raise AmazonReportError("Amazon reports are still processing; try again shortly.")
        return await self._assemble(chunks, docs)

    async def _assemble(self, chunks: list[ChunkRef], docs: list[dict]) -> SalesReport:
        start = min(c.start for c in chunks)
        end = max(c.end for c in chunks)
        asins = sorted(
            {
                r.get("childAsin") or r.get("parentAsin")
                for d in docs
                for r in d.get("salesAndTrafficByAsin", [])
                if r.get("childAsin") or r.get("parentAsin")
            }
        )
        titles: dict[str, str] = {}
        title_warning = None
        try:
            titles = await self._api().catalog_titles(asins) if asins else {}
        except Exception as exc:  # noqa: BLE001 - names are optional; sales data is not
            title_warning = f"Product names unavailable: {getattr(exc, 'message', exc)}"
        report = build_report(docs, start, end, titles)
        if title_warning:
            report.warnings.append(title_warning)
        return report

    async def product_trend(self, start: date, end: date, sku: str | None, asin: str | None):
        if not (sku or asin):
            raise AmazonDateRangeError("Provide sku or asin.")
        self.validate_range(start, end)
        rows = await self._api().order_metrics(start, end, sku=sku, asin=asin)
        out = []
        for r in rows:
            total = r.get("totalSales") or {}
            out.append(
                DailyPoint(
                    date=date.fromisoformat(r["interval"][:10]),
                    units_ordered=int(r.get("unitCount", 0)),
                    ordered_revenue=Decimal(str(total.get("amount", 0))),
                    order_items=r.get("orderItemCount"),
                )
            )
        _mark_success()
        return out

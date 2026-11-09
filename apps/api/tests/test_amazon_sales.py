"""Amazon sales analytics -- all Amazon traffic is mocked (httpx.MockTransport)."""

from __future__ import annotations

import gzip
import io
import json
from datetime import date, timedelta
from decimal import Decimal

import httpx
import pytest
from app.integrations.amazon.client import AmazonClient
from app.integrations.amazon.config import AmazonConfig
from app.integrations.amazon.errors import (
    AmazonAuthError,
    AmazonDateRangeError,
    AmazonNotConfiguredError,
    AmazonPermissionError,
    AmazonRateLimitError,
    AmazonReportError,
)
from app.integrations.amazon.reports import AmazonReports
from app.schemas.amazon_sales import ChunkRef
from app.services.amazon_export import report_to_xlsx
from app.services.amazon_sales_service import (
    AmazonSalesService,
    build_report,
    filter_products,
    split_chunks,
)
from httpx import ASGITransport, AsyncClient
from openpyxl import load_workbook

CFG = AmazonConfig(
    client_id="cid",
    client_secret="sec",
    refresh_token="rt",
    marketplace_id="A21TJRUUN4KGV",
    region="eu",
)
DAY1 = date.today() - timedelta(days=3)
DAY2 = DAY1 + timedelta(days=1)


def money(a):
    return {"amount": a, "currencyCode": "INR"}


def doc(extra_asin_rows=None):
    return {
        "salesAndTrafficByDate": [
            {
                "date": DAY1.isoformat(),
                "salesByDate": {
                    "orderedProductSales": money(1000.50),
                    "unitsOrdered": 10,
                    "totalOrderItems": 8,
                    "shippedProductSales": money(900),
                    "unitsShipped": 9,
                    "unitsRefunded": 1,
                },
            },
            {
                "date": DAY2.isoformat(),
                "salesByDate": {
                    "orderedProductSales": money(499.50),
                    "unitsOrdered": 5,
                    "totalOrderItems": 5,
                    "shippedProductSales": money(400),
                    "unitsShipped": 4,
                    "unitsRefunded": 0,
                },
            },
        ],
        "salesAndTrafficByAsin": [
            {
                "parentAsin": "P1",
                "childAsin": "B001",
                "sku": "SKU-A",
                "salesByAsin": {"unitsOrdered": 10, "orderedProductSales": money(1000.50)},
            },
            {
                "parentAsin": "P2",
                "childAsin": "B002",
                "sku": "SKU-B",
                "salesByAsin": {"unitsOrdered": 5, "orderedProductSales": money(499.50)},
            },
            *(extra_asin_rows or []),
        ],
    }


class FakeAmazon:
    """Stateful mock of LWA + SP-API endpoints."""

    def __init__(
        self,
        *,
        statuses=("DONE",),
        lwa_status=200,
        api_status=200,
        rate_limits=0,
        document=None,
        gzip_doc=False,
    ):
        self.statuses = list(statuses)
        self.lwa_status = lwa_status
        self.api_status = api_status
        self.rate_limits = rate_limits
        self.document = document or doc()
        self.gzip_doc = gzip_doc
        self.calls: list[str] = []
        self.created: list[dict] = []

    def handler(self, req: httpx.Request) -> httpx.Response:
        path = req.url.path
        self.calls.append(f"{req.method} {path}")
        if "auth/o2/token" in path:
            if self.lwa_status != 200:
                return httpx.Response(
                    self.lwa_status,
                    json={"error": "invalid_grant", "error_description": "bad refresh token"},
                )
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        if path.startswith("/doc-download"):
            raw = json.dumps(self.document).encode()
            return httpx.Response(200, content=gzip.compress(raw) if self.gzip_doc else raw)
        assert req.headers["x-amz-access-token"] == "tok"
        if self.api_status != 200:
            return httpx.Response(
                self.api_status,
                json={"errors": [{"code": "Unauthorized", "message": "Access denied"}]},
            )
        if self.rate_limits > 0 and "reports" in path and req.method == "POST":
            self.rate_limits -= 1
            return httpx.Response(429, headers={"Retry-After": "1"}, json={"errors": []})
        if req.method == "POST" and path.endswith("/reports"):
            body = json.loads(req.content)
            self.created.append(body)
            return httpx.Response(202, json={"reportId": f"R{len(self.created)}"})
        if "/reports/2021-06-30/reports/" in path:
            st = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
            out = {"processingStatus": st}
            if st == "DONE":
                out["reportDocumentId"] = "D1"
            return httpx.Response(200, json=out)
        if "/documents/" in path:
            return httpx.Response(
                200,
                json={
                    "url": "https://tmp.example/doc-download",
                    **({"compressionAlgorithm": "GZIP"} if self.gzip_doc else {}),
                },
            )
        if path == "/sales/v1/orderMetrics":
            return httpx.Response(
                200,
                json={
                    "payload": [
                        {
                            "interval": f"{DAY1}T00:00:00+05:30--{DAY2}T00:00:00+05:30",
                            "unitCount": 3,
                            "orderItemCount": 2,
                            "totalSales": money("300.00"),
                        }
                    ]
                },
            )
        if path == "/catalog/2022-04-01/items":
            return httpx.Response(
                200,
                json={
                    "items": [{"asin": "B001", "summaries": [{"itemName": "Ashwagandha Capsules"}]}]
                },
            )
        return httpx.Response(404, json={"errors": [{"code": "NotFound", "message": path}]})


def make_service(fake: FakeAmazon, cfg=CFG):
    async def nosleep(_):
        return None

    http = httpx.AsyncClient(transport=httpx.MockTransport(fake.handler))
    client = AmazonClient(cfg, http_client=http, sleeper=nosleep, rates={}, buckets={})
    return AmazonSalesService(cfg, client)


async def run(svc: AmazonSalesService):
    chunks = await svc.start_fetch(DAY1, DAY2)
    return chunks, await svc.poll(chunks)


# --- authentication -------------------------------------------------------


async def test_authentication_failure_is_clear():
    svc = make_service(FakeAmazon(lwa_status=400))
    with pytest.raises(AmazonAuthError, match="invalid_grant"):
        await svc.start_fetch(DAY1, DAY2)


async def test_missing_credentials_reports_missing_vars():
    cfg = AmazonConfig(None, None, None, None, None)
    svc = make_service(FakeAmazon(), cfg)
    assert not svc.status().configured
    assert "AMAZON_SP_API_REFRESH_TOKEN" in svc.status().missing
    with pytest.raises(AmazonNotConfiguredError):
        await svc.start_fetch(DAY1, DAY2)


async def test_missing_permission_403():
    svc = make_service(FakeAmazon(api_status=403))
    with pytest.raises(AmazonPermissionError, match="permission"):
        await svc.start_fetch(DAY1, DAY2)


# --- rate limits ----------------------------------------------------------


async def test_rate_limit_retries_then_succeeds():
    fake = FakeAmazon(rate_limits=2)
    chunks = await make_service(fake).start_fetch(DAY1, DAY2)
    assert chunks[0].report_id == "R1"
    assert sum(c.startswith("POST") and c.endswith("/reports") for c in fake.calls) == 3


async def test_rate_limit_exhausted_raises():
    with pytest.raises(AmazonRateLimitError):
        await make_service(FakeAmazon(rate_limits=99)).start_fetch(DAY1, DAY2)


# --- report generation / processing --------------------------------------


async def test_processing_then_done():
    svc = make_service(FakeAmazon(statuses=["IN_QUEUE", "IN_PROGRESS", "DONE"]))
    chunks = await svc.start_fetch(DAY1, DAY2)
    assert (await svc.poll(chunks)).state == "processing"
    assert (await svc.poll(chunks)).state == "processing"
    done = await svc.poll(chunks)
    assert done.state == "done" and done.report is not None


@pytest.mark.parametrize("st", ["FATAL", "CANCELLED"])
async def test_failed_report_states(st):
    svc = make_service(FakeAmazon(statuses=[st]))
    chunks = await svc.start_fetch(DAY1, DAY2)
    with pytest.raises(AmazonReportError, match=st):
        await svc.poll(chunks)


async def test_gzip_document_is_decoded_in_memory():
    _, res = await run(make_service(FakeAmazon(gzip_doc=True)))
    assert res.report.summary.total_units_ordered == 15


# --- daily / product sales and revenue calculations ----------------------


async def test_daily_and_summary_numbers():
    _, res = await run(make_service(FakeAmazon()))
    r = res.report
    assert [d.units_ordered for d in r.daily] == [10, 5]
    assert r.summary.total_units_ordered == 15
    assert r.summary.total_ordered_revenue == Decimal("1500.00")
    assert r.summary.products_sold == 2
    assert r.summary.total_units_shipped == 13
    assert r.summary.total_shipped_revenue == Decimal("1300")
    assert r.summary.currency == "INR"
    assert r.warnings == [] or all("Product names" not in w for w in r.warnings)


async def test_product_rows_names_asp_and_unavailable_shipped():
    _, res = await run(make_service(FakeAmazon()))
    rows = {p.sku: p for p in res.report.products}
    assert rows["SKU-A"].product_name == "Ashwagandha Capsules"
    assert rows["SKU-A"].avg_selling_price == Decimal("100.05")
    assert rows["SKU-B"].product_name is None  # not invented
    assert rows["SKU-A"].units_shipped is None  # unavailable, not 0
    assert "product.units_shipped" in res.report.unavailable
    assert [p.sku for p in res.report.products] == ["SKU-A", "SKU-B"]  # by revenue desc


def test_zero_units_has_no_average_price():
    d = doc(
        [
            {
                "childAsin": "B003",
                "sku": "Z",
                "salesByAsin": {"unitsOrdered": 0, "orderedProductSales": money(0)},
            }
        ]
    )
    r = build_report([d], DAY1, DAY2, {})
    z = next(p for p in r.products if p.sku == "Z")
    assert z.avg_selling_price is None
    assert r.summary.products_sold == 2  # zero-unit product not counted


def test_no_double_counting_across_windows_and_duplicate_dates():
    d1, d2 = doc(), doc()  # same date and same ASIN appear in two windows
    r = build_report([d1, d2], DAY1, DAY2, {})
    assert r.summary.total_units_ordered == 15  # dates dedupe, not 30
    a = next(p for p in r.products if p.sku == "SKU-A")
    assert a.units_ordered == 20  # distinct windows are summed per (asin, sku)


def test_mismatch_warning_and_missing_shipped_unavailable():
    d = doc()
    for row in d["salesAndTrafficByDate"]:
        del row["salesByDate"]["unitsShipped"], row["salesByDate"]["shippedProductSales"]
    d["salesAndTrafficByAsin"][0]["salesByAsin"]["unitsOrdered"] = 11
    r = build_report([d], DAY1, DAY2, {})
    assert r.summary.total_units_shipped is None and r.summary.total_shipped_revenue is None
    assert any("differs" in w or "vs daily total" in w for w in r.warnings)


def test_weekly_monthly_aggregation():
    d = {
        "salesAndTrafficByDate": [
            {
                "date": "2026-10-05",
                "salesByDate": {"orderedProductSales": money(10), "unitsOrdered": 1},
            },
            {
                "date": "2026-10-11",
                "salesByDate": {"orderedProductSales": money(20), "unitsOrdered": 2},
            },
            {
                "date": "2026-10-12",
                "salesByDate": {"orderedProductSales": money(30), "unitsOrdered": 3},
            },
            {
                "date": "2026-11-01",
                "salesByDate": {"orderedProductSales": money(40), "unitsOrdered": 4},
            },
        ],
        "salesAndTrafficByAsin": [],
    }
    r = build_report([d], date(2026, 10, 1), date(2026, 11, 2), {})
    assert [(w.period, w.units_ordered) for w in r.weekly] == [
        ("2026-10-05", 3),
        ("2026-10-12", 3),
        ("2026-10-26", 4),
    ]
    assert [(m.label, m.units_ordered, m.ordered_revenue) for m in r.monthly] == [
        ("2026-10", 6, Decimal(60)),
        ("2026-11", 4, Decimal(40)),
    ]
    assert r.weekly[0].units_shipped is None  # unavailable, not 0


# --- date filtering -------------------------------------------------------


def test_rows_outside_requested_range_are_ignored():
    r = build_report([doc()], DAY2, DAY2, {})
    assert r.summary.total_units_ordered == 5


def test_split_chunks_are_disjoint_30_day_windows():
    chunks = split_chunks(date(2026, 1, 1), date(2026, 3, 15))
    assert chunks[0] == (date(2026, 1, 1), date(2026, 1, 30))
    assert chunks[-1][1] == date(2026, 3, 15)
    for (_, e), (s, _) in zip(chunks, chunks[1:], strict=False):
        assert s == e + timedelta(days=1)


async def test_date_range_restrictions():
    svc = make_service(FakeAmazon())
    with pytest.raises(AmazonDateRangeError, match="future"):
        await svc.start_fetch(DAY1, date.today() + timedelta(days=30))
    with pytest.raises(AmazonDateRangeError, match="before"):
        await svc.start_fetch(DAY2, DAY1)
    with pytest.raises(AmazonDateRangeError, match="too long"):
        await svc.start_fetch(date.today() - timedelta(days=200), date.today() - timedelta(days=1))


async def test_long_range_creates_multiple_report_windows():
    fake = FakeAmazon()
    chunks = await make_service(fake).start_fetch(
        date.today() - timedelta(days=60), date.today() - timedelta(days=1)
    )
    assert len(chunks) == 2 and len(fake.created) == 2


def test_product_sku_filter():
    r = build_report([doc()], DAY1, DAY2, {"B001": "Ashwagandha Capsules"})
    assert [p.sku for p in filter_products(r.products, "sku-b")] == ["SKU-B"]
    assert [p.sku for p in filter_products(r.products, "ashwa")] == ["SKU-A"]
    assert len(filter_products(r.products, "")) == 2


async def test_product_trend_uses_sales_api():
    pts = await make_service(FakeAmazon()).product_trend(DAY1, DAY2, "SKU-A", None)
    assert pts[0].units_ordered == 3 and pts[0].ordered_revenue == Decimal("300.00")


# --- Excel export ---------------------------------------------------------


async def test_excel_export_contents_and_injection_safety():
    d = doc(
        [
            {
                "childAsin": "B009",
                "sku": "=HYPERLINK()",
                "salesByAsin": {"unitsOrdered": 1, "orderedProductSales": money(5)},
            }
        ]
    )
    r = build_report([d], DAY1, DAY2, {"B009": "=cmd()"})
    wb = load_workbook(io.BytesIO(report_to_xlsx(r)))
    assert wb.sheetnames == ["Summary", "Daily", "Weekly", "Monthly", "Products", "Definitions"]
    prods = list(wb["Products"].iter_rows(values_only=True))
    assert prods[0][0] == "Product name"
    flat = [c for row in prods for c in row]
    assert "Unavailable" in flat  # units shipped
    assert "'=cmd()" in flat and "=cmd()" not in flat
    assert "'=HYPERLINK()" in flat
    assert wb["Summary"]["B6"].value == 1500.0


# --- no persistence -------------------------------------------------------


def test_no_amazon_tables_or_persistence_code():
    import pathlib

    import app.models  # noqa: F401
    from app.db.base import Base

    assert not [t for t in Base.metadata.tables if "amazon" in t.lower()]
    root = pathlib.Path(__file__).parent.parent / "app"
    sources = [
        *root.glob("integrations/amazon/*.py"),
        root / "services/amazon_sales_service.py",
        root / "services/amazon_export.py",
        root / "api/v1/endpoints/amazon_sales.py",
    ]
    for f in sources:
        text = f.read_text(encoding="utf-8")
        for banned in (
            "AsyncSession",
            "sqlalchemy",
            "redis",
            "tempfile",
            "open(",
            "write_bytes",
            "write_text",
            "pickle",
            "shelve",
        ):
            assert banned not in text, f"{banned} found in {f.name}"


async def test_service_writes_no_files(tmp_path, monkeypatch):
    import builtins

    real_open = builtins.open
    writes = []

    def spy(file, mode="r", *a, **k):
        if any(c in mode for c in "wax+"):
            writes.append(file)
        return real_open(file, mode, *a, **k)

    monkeypatch.setattr(builtins, "open", spy)
    await run(make_service(FakeAmazon(gzip_doc=True)))
    assert writes == []


# --- HTTP endpoints -------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_report_window():
    AmazonReports.recent_creates.clear()
    yield
    AmazonReports.recent_creates.clear()


@pytest.fixture
async def api(monkeypatch):
    from app.api.v1.endpoints import amazon_sales as ep
    from app.main import app

    fake = FakeAmazon()
    svc = make_service(fake)
    app.dependency_overrides[ep.get_service] = lambda: svc
    app.dependency_overrides[ep._perm] = lambda: object()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        yield c, fake
    app.dependency_overrides.clear()


async def test_endpoints_flow_and_export(api):
    c, _ = api
    s = (await c.get("/api/v1/marketplaces/amazon/sales/status")).json()["data"]
    assert s["configured"] is True
    r = await c.post(
        "/api/v1/marketplaces/amazon/sales/reports",
        json={"date_from": DAY1.isoformat(), "date_to": DAY2.isoformat()},
    )
    chunks = r.json()["data"]
    p = await c.post(
        "/api/v1/marketplaces/amazon/sales/reports/poll", json={"chunks": chunks, "q": "SKU-A"}
    )
    data = p.json()["data"]
    assert data["state"] == "done" and len(data["report"]["products"]) == 1
    assert data["report"]["summary"]["total_units_ordered"] == 15
    x = await c.post("/api/v1/marketplaces/amazon/sales/reports/export", json={"chunks": chunks})
    assert x.status_code == 200 and x.content[:2] == b"PK"
    st = (await c.get("/api/v1/marketplaces/amazon/sales/status")).json()["data"]
    assert st["last_successful_fetch_at"] is not None


async def test_endpoint_error_envelope_for_bad_range(api):
    c, _ = api
    r = await c.post(
        "/api/v1/marketplaces/amazon/sales/reports",
        json={"date_from": DAY2.isoformat(), "date_to": DAY1.isoformat()},
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "amazon_invalid_date_range"


def test_chunkref_is_only_a_handle():
    assert set(ChunkRef.model_fields) == {"report_id", "start", "end"}


# --- documented limits (verified against developer-docs.amazon) ---------


async def test_report_window_limit_three_per_five_minutes():
    svc = make_service(FakeAmazon())
    for _ in range(3):
        await svc.start_fetch(DAY1, DAY2)
    with pytest.raises(AmazonRateLimitError, match="3 Sales & Traffic"):
        await svc.start_fetch(DAY1, DAY2)


async def test_multi_window_range_refused_before_creating_any_report():
    fake = FakeAmazon()
    svc = make_service(fake)
    AmazonReports.recent_creates.extend([__import__("time").monotonic()] * 2)  # 1 slot left
    with pytest.raises(AmazonRateLimitError):
        await svc.start_fetch(date.today() - timedelta(days=60), date.today() - timedelta(days=1))
    assert fake.created == []


async def test_lookback_limited_to_two_years():
    svc = make_service(FakeAmazon())
    with pytest.raises(AmazonDateRangeError, match="2 years"):
        await svc.start_fetch(
            date.today() - timedelta(days=800), date.today() - timedelta(days=790)
        )


async def test_token_bucket_is_shared_and_never_blocks_long():
    from app.integrations.amazon.client import AmazonClient as C

    fake = FakeAmazon()
    shared: dict = {}
    slept: list[float] = []

    async def rec(d):
        slept.append(d)

    def mk():
        http = httpx.AsyncClient(transport=httpx.MockTransport(fake.handler))
        return C(
            CFG,
            http_client=http,
            sleeper=rec,
            rates={"getReportDocument": (0.0167, 2)},
            buckets=shared,
        )

    await mk().request("getReportDocument", "GET", "/reports/2021-06-30/documents/D1")
    await mk().request("getReportDocument", "GET", "/reports/2021-06-30/documents/D1")
    # burst of 2 used up across two *different* client instances -> third refused (wait ~60s)
    with pytest.raises(AmazonRateLimitError, match="burst"):
        await mk().request("getReportDocument", "GET", "/reports/2021-06-30/documents/D1")
    assert slept == []


async def test_sales_api_request_shape():
    seen = {}
    fake = FakeAmazon()
    orig = fake.handler

    def spy(req):
        if req.url.path == "/sales/v1/orderMetrics":
            seen.update(dict(req.url.params))
        return orig(req)

    fake.handler = spy
    await make_service(fake).product_trend(DAY1, DAY2, "SKU-A", None)
    assert seen["granularityTimeZone"] == "Asia/Kolkata"
    assert seen["interval"].endswith(f"{DAY2 + timedelta(days=1)}T00:00:00+05:30")  # exclusive end
    assert seen["sku"] == "SKU-A" and "asin" not in seen

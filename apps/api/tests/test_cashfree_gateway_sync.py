"""Module 7 -- Cashfree API-first synchronisation.

Synthetic data only; the Cashfree API is faked (`httpx.MockTransport` for the
HTTP shape, monkeypatched client methods for sync flows). No network call and
no real credentials.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
from app.core.config import settings
from app.integrations.cashfree.client import CashfreeClient
from app.integrations.cashfree.config import CashfreeConfig
from app.integrations.cashfree.errors import CashfreeApiError
from app.integrations.cashfree.normalizer import (
    normalize_recon_row,
    normalize_settlement_recon_row,
)
from app.integrations.retry import RetryPolicy
from app.main import app
from app.models.cashfree_settlement import CashfreeSettlement
from app.models.enums import PaymentType
from app.models.remittance_reconciliation import (
    BankCredit,
    BankCreditLink,
    GatewaySettlementLine,
    GatewaySyncState,
)
from app.services.cashfree_gateway_sync_service import (
    CashfreeGatewaySyncService,
    derive_line_status,
)
from app.tasks import cashfree_gateway_sync as task_module
from app.tasks.cashfree_gateway_sync import sync_cashfree_manual_task
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.telecalling_test_utils import bearer_client, make_order, make_role, make_user

pytestmark = pytest.mark.asyncio

SECRET = "test-cashfree-client-secret"
CLIENT_ID = "test-client-id"
BASE = "/api/v1/remittance-reconciliation/prepaid"
NOW = datetime.now(UTC).replace(microsecond=0)
D = Decimal


@pytest.fixture(autouse=True)
def _cashfree_configured(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "CASHFREE_CLIENT_ID", CLIENT_ID)
    monkeypatch.setattr(settings, "CASHFREE_CLIENT_SECRET", SECRET)

    async def _noop(self) -> None:
        return None

    monkeypatch.setattr(CashfreeClient, "aclose", _noop)
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def unconfigured(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "CASHFREE_CLIENT_ID", None)
    monkeypatch.setattr(settings, "CASHFREE_CLIENT_SECRET", None)


def _fake_queue(monkeypatch) -> list[dict]:
    """Captures what the API publishes instead of talking to a broker."""
    published: list[dict] = []
    monkeypatch.setattr(
        sync_cashfree_manual_task, "apply_async", lambda **kwargs: published.append(kwargs)
    )
    return published


async def _work(db, published: list[dict]) -> str:
    """What the Celery worker does with the one queued message."""
    (message,) = published

    @contextlib.asynccontextmanager
    async def _session():
        yield db

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(task_module, "AsyncSessionLocal", _session)
        return await task_module._run_manual(*message["args"])


# --- Fake Cashfree -----------------------------------------------------------


def recon_row(
    *,
    event_id: str,
    order_id: str,
    cf_payment_id: str,
    cf_settlement_id: str = "S1",
    utr: str | None = "UTR1",
    event_type: str = "PAYMENT",
    amount: str = "1000.00",
    settlement_amount: str = "976.40",
    charge: str = "20.00",
    tax: str = "3.60",
    event_status: str = "SUCCESS",
) -> dict:
    """A row shaped like the official `/settlement/recon` response."""
    return {
        "event_details": {
            "event_id": event_id,
            "event_type": event_type,
            "event_settlement_amount": settlement_amount,
            "event_amount": amount,
            "event_status": event_status,
            "event_time": "2026-11-20T10:00:00Z",
            "event_service_charge": charge,
            "event_service_tax": tax,
        },
        "order_details": {"order_id": order_id},
        "payment_details": {"cf_payment_id": cf_payment_id, "status": "SUCCESS"},
        "settlement_details": {
            "cf_settlement_id": cf_settlement_id,
            "settlement_utr": utr,
            "settlement_date": "2026-11-22T06:00:00Z",
        },
    }


def lot_row(
    cf_settlement_id: str = "S1", status: str = "SUCCESS", utr: str | None = "UTR1"
) -> dict:
    return {
        "cf_settlement_id": cf_settlement_id,
        "status": status,
        "settlement_utr": utr,
        "settlement_initiated_on": "2026-11-21T00:00:00Z",
        "settlement_processed_on": "2026-11-22T06:00:00Z" if status == "SUCCESS" else None,
        "payment_amount": "1000.00",
        "pg_service_charge": "20.00",
        "pg_service_tax": "3.60",
        "amount_settled": "976.40",
    }


class FakeCashfree:
    """Serves paginated pages keyed off the cursor the caller sends back."""

    def __init__(self, monkeypatch, *, lines=None, lots=None, txns=None):
        self.lines = lines if lines is not None else [[]]
        self.lots = lots if lots is not None else [[]]
        self.txns = txns if txns is not None else [[]]
        self.calls: list[tuple[str, dict]] = []
        self.fail_lines: CashfreeApiError | None = None
        self.fail_lots: CashfreeApiError | None = None
        outer = self

        def page(pages, cursor):
            i = int(cursor.split("-")[1]) if cursor else 0
            nxt = f"cursor-{i + 1}" if i + 1 < len(pages) else None
            return {"data": pages[i], "cursor": nxt}

        async def recon(self, *, start_date, end_date, cursor=None, limit=1000):
            outer.calls.append(("recon", {"cursor": cursor}))
            return page(outer.txns, cursor)

        async def settlements(
            self, *, start_date=None, end_date=None, settlement_status=None, cursor=None, limit=1000
        ):
            outer.calls.append(("settlements", {"cursor": cursor}))
            if outer.fail_lots:
                raise outer.fail_lots
            return page(outer.lots, cursor)

        async def settlement_recon(self, *, cursor=None, **kwargs):
            outer.calls.append(("settlement_recon", {"cursor": cursor, **kwargs}))
            if outer.fail_lines:
                raise outer.fail_lines
            return page(outer.lines, cursor)

        monkeypatch.setattr(CashfreeClient, "get_reconciliation", recon)
        monkeypatch.setattr(CashfreeClient, "get_settlements", settlements)
        monkeypatch.setattr(CashfreeClient, "get_settlement_reconciliation", settlement_recon)

    def count(self, name: str) -> int:
        return sum(1 for n, _ in self.calls if n == name)


async def prepaid_order(db: AsyncSession, number: str, amount: str = "1000.00"):
    order = await make_order(
        db,
        order_number=number,
        payment_type=PaymentType.PREPAID,
        total_amount=D(amount),
        order_datetime=NOW - timedelta(days=5),
    )
    await db.commit()
    return order


async def lines(db: AsyncSession) -> list[GatewaySettlementLine]:
    return list((await db.execute(select(GatewaySettlementLine))).scalars().all())


# --- Client: official request shape, pagination, retry ------------------------

_CONFIG = CashfreeConfig(
    client_id=CLIENT_ID,
    client_secret=SECRET,
    api_version="2025-01-01",
    base_url="https://sandbox.cashfree.com/pg",
    webhook_secret=None,
    return_url=None,
)


async def test_client_settlement_recon_request_shape_and_limit_cap():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["method"] = request.method
        seen["body"] = json.loads(request.content)
        seen["headers"] = dict(request.headers)
        return httpx.Response(200, json={"data": [], "cursor": None})

    client = CashfreeClient(
        _CONFIG, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    await client.get_settlement_reconciliation(
        start_date="2026-11-01T00:00:00Z", end_date="2026-11-30T00:00:00Z", cursor="c1", limit=5000
    )
    assert seen["method"] == "POST"
    assert seen["url"] == "https://sandbox.cashfree.com/pg/settlement/recon"
    assert seen["body"] == {
        "pagination": {"limit": 1000, "cursor": "c1"},
        "filters": {
            "start_date_initiated_on": "2026-11-01T00:00:00Z",
            "end_date_initiated_on": "2026-11-30T00:00:00Z",
        },
    }
    assert seen["headers"]["x-client-id"] == CLIENT_ID


async def test_client_settlement_recon_by_settlement_id_uses_only_that_filter():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"data": [], "cursor": None})

    client = CashfreeClient(
        _CONFIG, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    await client.get_settlement_reconciliation(cf_settlement_ids=["S1"], start_date="x")
    assert seen["body"]["filters"] == {"cf_settlement_ids": ["S1"]}


async def test_client_retries_rate_limit_then_succeeds():
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] == 1:
            return httpx.Response(429, json={"message": "slow down"})
        return httpx.Response(200, json={"data": [], "cursor": None})

    client = CashfreeClient(
        _CONFIG,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        retry_policy=RetryPolicy(max_retries=2, base_delay_seconds=0.0, max_delay_seconds=0.0),
    )
    result = await client.get_settlement_reconciliation(start_date="a", end_date="b")
    assert result["data"] == [] and attempts["n"] == 2


async def test_client_credential_rejection_is_an_authentication_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"message": "forbidden"})

    client = CashfreeClient(
        _CONFIG, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    with pytest.raises(CashfreeApiError) as exc:
        await client.get_settlement_reconciliation(start_date="a", end_date="b")
    assert exc.value.error_type == "authentication_error"


# --- Normalisation -------------------------------------------------------------


async def test_normalizer_reads_nested_official_row():
    row = normalize_settlement_recon_row(
        recon_row(event_id="E1", order_id="AWL1", cf_payment_id="P1")
    )
    assert row is not None
    assert row["order_ref"] == "AWL1" and row["gateway_transaction_id"] == "P1"
    assert row["cf_settlement_id"] == "S1" and row["settlement_reference"] == "UTR1"
    assert row["gross_amount"] == D("1000.00")
    assert row["gateway_charges"] == D("20.00") and row["gst_amount"] == D("3.60")
    assert row["net_amount"] == D("976.40")
    assert row["line_status"] == "success" and row["event_type"] == "PAYMENT"
    assert row["settlement_date"] is not None


async def test_normalizer_signs_refund_negative_and_reversal_positive():
    refund = normalize_settlement_recon_row(
        recon_row(
            event_id="R1",
            order_id="A",
            cf_payment_id="P",
            event_type="REFUND",
            amount="300.00",
            settlement_amount="300.00",
            charge="0",
            tax="0",
        )
    )
    assert refund["net_amount"] == D("-300.00") and refund["refund_amount"] == D("300.00")
    refund_neg = normalize_settlement_recon_row(
        recon_row(
            event_id="R2",
            order_id="A",
            cf_payment_id="P",
            event_type="REFUND",
            amount="300.00",
            settlement_amount="-300.00",
            charge="0",
            tax="0",
        )
    )
    assert refund_neg["net_amount"] == D("-300.00")  # not double-negated
    reversal = normalize_settlement_recon_row(
        recon_row(
            event_id="R3",
            order_id="A",
            cf_payment_id="P",
            event_type="REFUND_REVERSAL",
            amount="300.00",
            settlement_amount="-300.00",
            charge="0",
            tax="0",
        )
    )
    assert reversal["net_amount"] == D("300.00") and reversal["refund_amount"] == D("0")


async def test_normalizer_never_invents_a_missing_settlement_amount():
    raw = recon_row(event_id="E9", order_id="AWL1", cf_payment_id="P1")
    del raw["event_details"]["event_settlement_amount"]
    assert normalize_settlement_recon_row(raw) is None


async def test_existing_transaction_normalizer_now_reads_nested_rows():
    nested = {
        "event_details": {"event_type": "PAYMENT", "event_id": "E1"},
        "order_details": {"order_id": "AWL1"},
        "payment_details": {
            "cf_payment_id": "P1",
            "payment_amount": "1000.00",
            "payment_currency": "INR",
            "payment_time": "2026-11-20T10:00:00Z",
            "status": "SUCCESS",
        },
    }
    out = normalize_recon_row(nested)
    assert out is not None
    assert out["cashfree_order_id"] == "AWL1" and out["cf_payment_id"] == "P1"
    assert out["raw_status"] == "SUCCESS" and out["amount"] == D("1000.00")
    refund = {**nested, "event_details": {"event_type": "REFUND"}}
    assert normalize_recon_row(refund) is None
    # The flat shape still works.
    assert normalize_recon_row({"order_id": "X", "cf_payment_id": "Y", "status": "SUCCESS"})


async def test_line_status_rules():
    assert derive_line_status("success", "SUCCESS", True) == "success"
    assert derive_line_status("success", "PENDING_WITH_BANK", True) == "pending"
    assert derive_line_status("success", "FAILED", True) == "failed"
    assert derive_line_status("success", "REVERSED", True) == "reversed"
    assert derive_line_status("success", None, False) == "pending"  # no lot, no UTR
    assert derive_line_status("success", None, True) == "success"


# --- Sync service --------------------------------------------------------------


async def test_not_configured_makes_no_call_and_creates_no_data(
    db_session, unconfigured, monkeypatch
):
    fake = FakeCashfree(
        monkeypatch, lines=[[recon_row(event_id="E1", order_id="A", cf_payment_id="P")]]
    )
    result = await CashfreeGatewaySyncService(db_session).sync(trigger="manual")
    assert result["status"] == "not_configured"
    assert fake.calls == []
    assert await lines(db_session) == []
    status = await CashfreeGatewaySyncService(db_session).status()
    assert status["connection_status"] == "not_configured" and status["configured"] is False
    assert status["last_successful_sync_at"] is None


async def test_full_sync_pages_matches_and_is_idempotent(db_session, monkeypatch):
    o1 = await prepaid_order(db_session, "#AWL1")
    await prepaid_order(db_session, "#AWL2", "500.00")
    FakeCashfree(
        monkeypatch,
        lines=[
            [recon_row(event_id="E1", order_id="AWL1", cf_payment_id="P1")],
            [
                recon_row(
                    event_id="E2",
                    order_id="AWL2",
                    cf_payment_id="P2",
                    amount="500.00",
                    settlement_amount="488.20",
                    charge="10.00",
                    tax="1.80",
                ),
                recon_row(event_id="E3", order_id="UNKNOWN", cf_payment_id="P3"),
            ],
        ],
        lots=[[lot_row()]],
    )
    svc = CashfreeGatewaySyncService(db_session)
    first = await svc.sync(trigger="manual")
    assert first["status"] == "ok", first
    counts = first["steps"]["settlement_lines"]
    assert counts["pages"] == 2 and counts["fetched"] == 3 and counts["new"] == 3
    assert counts["auto_matched"] == 2  # UNKNOWN stays unmatched, visible
    stored = {ln.gateway_transaction_id: ln for ln in await lines(db_session)}
    assert stored["P1"].matched_order_id == o1.id and stored["P1"].match_method == "order_ref"
    assert stored["P1"].source == "api" and stored["P1"].synced_at is not None
    assert stored["P1"].cf_settlement_id == "S1" and stored["P1"].settlement_reference == "UTR1"
    assert stored["P3"].matched_order_id is None
    assert stored["P1"].gateway == "Cashfree"

    second = await svc.sync(trigger="scheduled")
    assert second["status"] == "ok"
    again = second["steps"]["settlement_lines"]
    assert again["new"] == 0 and again["updated"] == 0 and again["unchanged"] == 3
    assert (await db_session.execute(select(func.count(GatewaySettlementLine.id)))).scalar() == 3
    assert (await db_session.execute(select(func.count(CashfreeSettlement.id)))).scalar() == 1


async def test_resync_updates_changed_line_in_place_and_keeps_manual_match(db_session, monkeypatch):
    other = await prepaid_order(db_session, "#AWL7", "1000.00")
    fake = FakeCashfree(
        monkeypatch,
        lines=[[recon_row(event_id="E1", order_id="NOPE", cf_payment_id="P1")]],
        lots=[[lot_row()]],
    )
    svc = CashfreeGatewaySyncService(db_session)
    await svc.sync(trigger="manual")
    line = (await lines(db_session))[0]
    assert line.matched_order_id is None
    line.matched_order_id, line.match_method = other.id, "manual"
    await db_session.commit()

    fake.lines = [
        [
            recon_row(
                event_id="E1",
                order_id="NOPE",
                cf_payment_id="P1",
                settlement_amount="970.00",
                charge="24.00",
                tax="6.00",
            )
        ]
    ]
    result = await svc.sync(trigger="manual")
    assert result["steps"]["settlement_lines"]["updated"] == 1
    rows = await lines(db_session)
    assert len(rows) == 1 and rows[0].net_amount == D("970.00")
    assert rows[0].matched_order_id == other.id and rows[0].match_method == "manual"


async def test_pending_settlement_is_never_counted_as_settled(db_session, monkeypatch):
    await prepaid_order(db_session, "#AWL1")
    FakeCashfree(
        monkeypatch,
        lines=[[recon_row(event_id="E1", order_id="AWL1", cf_payment_id="P1", utr=None)]],
        lots=[[lot_row(status="PENDING_WITH_BANK", utr=None)]],
    )
    await CashfreeGatewaySyncService(db_session).sync(trigger="manual")
    assert (await lines(db_session))[0].line_status == "pending"
    user = await make_user(
        db_session,
        email="r@x.com",
        role=await make_role(db_session, name="R", permission_codes=["reconciliation.read"]),
    )
    async with bearer_client(
        app, __import__("app.db.session", fromlist=["get_db"]).get_db, db_session, user.id
    ) as client:
        rec = (await client.get(f"{BASE}/records", params=_dp())).json()["data"]
    row = next(r for r in rec if r["order_number"].lstrip("#") == "AWL1")
    assert row["status"] == "pending" and row["line_count"] == 0
    assert D(row["actual_settlement"]) == 0


def _dp():
    return {
        "date_from": (NOW - timedelta(days=60)).isoformat(),
        "date_to": (NOW + timedelta(days=1)).isoformat(),
    }


async def test_cashfree_success_is_not_bank_confirmation_until_credit_linked(
    db_session, monkeypatch
):
    from app.db.session import get_db

    await prepaid_order(db_session, "#AWL1")
    FakeCashfree(
        monkeypatch,
        lines=[[recon_row(event_id="E1", order_id="AWL1", cf_payment_id="P1")]],
        lots=[[lot_row()]],
    )
    svc = CashfreeGatewaySyncService(db_session)
    await svc.sync(trigger="manual")
    user = await make_user(
        db_session,
        email="r2@x.com",
        role=await make_role(db_session, name="R2", permission_codes=["reconciliation.read"]),
    )

    async def status_of() -> str:
        async with bearer_client(app, get_db, db_session, user.id) as client:
            rec = (await client.get(f"{BASE}/records", params=_dp())).json()["data"]
        return next(r for r in rec if r["order_number"].lstrip("#") == "AWL1")["status"]

    # Settlement is SUCCESS in Cashfree, yet no bank credit -> NOT matched.
    assert await status_of() == "partially_reconciled"

    db_session.add(
        BankCredit(amount=D("976.40"), reference="UTR1", credit_date=NOW, dedupe_key="bank-1")
    )
    await db_session.commit()
    result = await svc.sync(trigger="scheduled")
    assert result["steps"]["settlement_lines"]["bank_links"] == 1
    assert await status_of() == "matched"
    assert (await db_session.execute(select(func.count(BankCreditLink.id)))).scalar() == 1


async def test_partial_failure_is_recorded_retried_and_does_not_advance_watermark(
    db_session, monkeypatch
):
    await prepaid_order(db_session, "#AWL1")
    fake = FakeCashfree(
        monkeypatch,
        lines=[[recon_row(event_id="E1", order_id="AWL1", cf_payment_id="P1")]],
        lots=[[lot_row()]],
    )
    fake.fail_lines = CashfreeApiError(
        "Cashfree API returned a server error (500).", error_type="http_500", status_code=500
    )
    svc = CashfreeGatewaySyncService(db_session)
    first = await svc.sync(trigger="scheduled")
    assert first["status"] == "partial"
    state = (await db_session.execute(select(GatewaySyncState))).scalar_one()
    assert state.last_success_at is None and state.synced_through is None
    assert state.last_status == "partial" and "settlement_lines" in (state.last_error or "")
    assert state.lock_until is None  # lock always released
    info = await svc.status()
    assert info["connection_status"] == "degraded" and info["last_successful_sync_at"] is None

    fake.fail_lines = None  # retry succeeds, same window, no duplicates
    second = await svc.sync(trigger="scheduled")
    assert second["status"] == "ok"
    assert len(await lines(db_session)) == 1
    state = (await db_session.execute(select(GatewaySyncState))).scalar_one()
    assert state.last_success_at is not None and state.synced_through is not None


async def test_next_sync_resumes_from_watermark_with_lookback(db_session, monkeypatch):
    fake = FakeCashfree(monkeypatch)
    svc = CashfreeGatewaySyncService(db_session)
    await svc.sync(trigger="manual")
    first_call = next(c for n, c in fake.calls if n == "settlement_recon")
    start1 = datetime.fromisoformat(first_call["start_date"].replace("Z", "+00:00"))
    assert (NOW - start1) >= timedelta(days=settings.CASHFREE_SYNC_INITIAL_DAYS - 1)
    fake.calls.clear()
    await svc.sync(trigger="scheduled")
    second = next(c for n, c in fake.calls if n == "settlement_recon")
    start2 = datetime.fromisoformat(second["start_date"].replace("Z", "+00:00"))
    assert (datetime.now(UTC) - start2) < timedelta(days=settings.CASHFREE_SYNC_LOOKBACK_DAYS + 1)


async def test_credential_rejection_reports_not_configured_without_leaking_secrets(
    db_session, monkeypatch
):
    fake = FakeCashfree(monkeypatch)
    fake.fail_lots = CashfreeApiError(
        f"Cashfree rejected the credentials ({SECRET}).",
        error_type="authentication_error",
        status_code=403,
    )
    from app.core.exceptions import IntegrationError  # noqa: F401

    svc = CashfreeGatewaySyncService(db_session)
    result = await svc.sync(trigger="manual")
    assert result["status"] == "auth_error"
    info = await svc.status()
    assert info["connection_status"] == "not_configured"
    assert SECRET not in json.dumps(info, default=str)
    assert SECRET not in json.dumps(result, default=str)


async def test_only_one_sync_runs_at_a_time(db_session, monkeypatch):
    fake = FakeCashfree(monkeypatch)
    svc = CashfreeGatewaySyncService(db_session)
    await svc.sync(trigger="manual")
    state = (await db_session.execute(select(GatewaySyncState))).scalar_one()
    state.lock_until = datetime.now(UTC) + timedelta(minutes=5)
    await db_session.commit()
    fake.calls.clear()
    result = await svc.sync(trigger="scheduled")
    assert result["status"] == "already_running" and fake.calls == []
    state = (await db_session.execute(select(GatewaySyncState))).scalar_one()
    state.lock_until = datetime.now(UTC) - timedelta(minutes=1)  # crashed run: lock expired
    await db_session.commit()
    assert (await svc.sync(trigger="scheduled"))["status"] == "ok"


async def test_celery_task_is_a_noop_when_disabled_or_unconfigured(db_session, monkeypatch):
    import app.tasks.cashfree_gateway_sync as task

    called = {"n": 0}

    class Boom:
        def __init__(self, *_a, **_k):
            called["n"] += 1

    monkeypatch.setattr(task, "CashfreeGatewaySyncService", Boom)
    monkeypatch.setattr(settings, "CASHFREE_SYNC_ENABLED", False)
    await task._run()
    assert called["n"] == 0


# --- API: status / Sync Now / RBAC ---------------------------------------------


async def _client(db, perms, email="u@x.com"):
    from app.db.session import get_db

    role = await make_role(db, name=f"R-{email}", permission_codes=perms)
    user = await make_user(db, email=email, role=role)
    return bearer_client(app, get_db, db, user.id)


async def test_status_endpoint_never_returns_credentials(db_session):
    async with await _client(db_session, ["reconciliation.read"]) as client:
        res = await client.get(f"{BASE}/cashfree/status")
    assert res.status_code == 200
    body = res.text
    assert SECRET not in body and CLIENT_ID not in body
    data = res.json()["data"]
    assert data["configured"] is True and data["connection_status"] == "never_synced"
    assert "not proof" in data["bank_confirmation_note"]


async def test_sync_now_requires_upload_permission(db_session, monkeypatch):
    FakeCashfree(monkeypatch)
    published = _fake_queue(monkeypatch)
    async with await _client(db_session, ["reconciliation.read"], "ro@x.com") as client:
        assert (await client.post(f"{BASE}/cashfree/sync")).status_code == 403
        assert (await client.get(f"{BASE}/cashfree/status")).status_code == 200
    async with await _client(
        db_session, ["reconciliation.read", "reconciliation.upload"], "up@x.com"
    ) as client:
        res = await client.post(f"{BASE}/cashfree/sync")
        assert res.status_code == 200 and res.json()["data"]["status"] == "queued"
        assert await _work(db_session, published) == "ok"
        status = (await client.get(f"{BASE}/cashfree/status")).json()["data"]
        assert status["connection_status"] == "connected"
        assert status["last_successful_sync_at"] is not None


async def test_sync_now_when_not_configured_returns_not_configured(
    db_session, unconfigured, monkeypatch
):
    fake = FakeCashfree(monkeypatch)
    async with await _client(
        db_session, ["reconciliation.read", "reconciliation.upload"]
    ) as client:
        res = await client.post(f"{BASE}/cashfree/sync")
        sources = (await client.get("/api/v1/remittance-reconciliation/sources")).json()["data"]
    assert res.json()["data"]["status"] == "not_configured"
    assert fake.calls == []
    cf = next(s for s in sources["prepaid"] if s["key"] == "cashfree")
    assert cf["api_configured"] is False and cf["connection_status"] == "not_configured"
    assert cf["note"].startswith("Not Configured")
    assert cf["upload_supported"] is True  # file upload remains the fallback


# --- Webhook --------------------------------------------------------------------


def _signed(payload: dict, secret: str = SECRET):
    body = json.dumps(payload).encode()
    ts = "1700000000000"
    sig = base64.b64encode(
        hmac.new(secret.encode(), ts.encode() + body, hashlib.sha256).digest()
    ).decode()
    return body, {
        "x-webhook-timestamp": ts,
        "x-webhook-signature": sig,
        "Content-Type": "application/json",
    }


def _wh(event_type: str, status: str, utr: str | None = "UTR1") -> dict:
    return {
        "type": event_type,
        "event_time": "2026-11-22T06:00:00Z",
        "data": {"settlement": {"settlement_id": "S1", "status": status, "utr": utr}},
    }


async def _post_wh(db, payload, **kwargs):
    from app.db.session import get_db
    from httpx import ASGITransport, AsyncClient

    async def _override():
        yield db

    app.dependency_overrides[get_db] = _override
    body, headers = _signed(payload, **kwargs)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.post(
            "/api/v1/webhooks/cashfree/settlement", content=body, headers=headers
        )


async def test_webhook_rejects_bad_signature(db_session):
    res = await _post_wh(db_session, _wh("SETTLEMENT_SUCCESS", "SUCCESS"), secret="wrong")
    assert res.status_code == 401
    assert (await db_session.execute(select(func.count(CashfreeSettlement.id)))).scalar() == 0


async def test_webhook_updates_lot_and_lines_and_never_regresses(db_session, monkeypatch):
    await prepaid_order(db_session, "#AWL1")
    FakeCashfree(
        monkeypatch,
        lines=[[recon_row(event_id="E1", order_id="AWL1", cf_payment_id="P1", utr=None)]],
        lots=[[lot_row(status="PENDING", utr=None)]],
    )
    await CashfreeGatewaySyncService(db_session).sync(trigger="manual")
    assert (await lines(db_session))[0].line_status == "pending"

    ok = await _post_wh(db_session, _wh("SETTLEMENT_SUCCESS", "SUCCESS"))
    assert ok.status_code == 200
    lot = (await db_session.execute(select(CashfreeSettlement))).scalar_one()
    await db_session.refresh(lot)
    assert lot.status == "SUCCESS" and lot.settlement_utr == "UTR1"
    line = (await lines(db_session))[0]
    await db_session.refresh(line)
    assert line.settlement_reference == "UTR1" and line.line_status == "success"
    state = (await db_session.execute(select(GatewaySyncState))).scalar_one()
    assert state.last_webhook_at is not None

    # A late/out-of-order INITIATED must not undo SUCCESS.
    await _post_wh(
        db_session,
        {**_wh("SETTLEMENT_INITIATED", "INITIATED"), "event_time": "2026-11-21T00:00:00Z"},
    )
    await db_session.refresh(lot)
    assert lot.status == "SUCCESS"

    # Reversal fails the lines so they stop counting as settled.
    await _post_wh(db_session, _wh("SETTLEMENT_REVERSED", "REVERSED"))
    await db_session.refresh(lot)
    await db_session.refresh(line)
    assert lot.status == "REVERSED" and line.line_status == "reversed"


async def test_webhook_duplicate_delivery_is_idempotent(db_session):
    first = await _post_wh(db_session, _wh("SETTLEMENT_SUCCESS", "SUCCESS"))
    second = await _post_wh(db_session, _wh("SETTLEMENT_SUCCESS", "SUCCESS"))
    assert first.status_code == second.status_code == 200
    assert (await db_session.execute(select(func.count(CashfreeSettlement.id)))).scalar() == 1


async def test_webhook_unknown_type_is_acked_and_ignored(db_session):
    res = await _post_wh(db_session, {"type": "PAYMENT_SUCCESS_WEBHOOK", "data": {}})
    assert res.status_code == 200
    assert (await db_session.execute(select(func.count(CashfreeSettlement.id)))).scalar() == 0


async def test_failed_step_with_a_real_user_still_records_state_and_releases_lock(
    db_session, monkeypatch
):
    """Regression: a rollback after a failed step expires the acting user;
    the audit/lock-release that follows must still work (found in browser
    verification of Sync Now)."""
    fake = FakeCashfree(monkeypatch, lots=[[lot_row()]])
    fake.fail_lines = CashfreeApiError(
        "Cashfree rejected the request payload (bad filter).",
        error_type="validation_error",
        status_code=400,
    )
    async with await _client(
        db_session, ["reconciliation.read", "reconciliation.upload"]
    ) as client:
        published = _fake_queue(monkeypatch)
        res = await client.post(f"{BASE}/cashfree/sync")
        assert res.status_code == 200, res.text
        assert await _work(db_session, published) == "partial"
        status = (await client.get(f"{BASE}/cashfree/status")).json()["data"]
    assert status["running"] is False and status["connection_status"] == "degraded"
    assert "rejected the request payload" in status["last_sync_error"]
    assert status["last_successful_sync_at"] is None

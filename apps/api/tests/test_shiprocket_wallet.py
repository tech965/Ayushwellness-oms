"""Shiprocket wallet: balance, pre-assignment guard, ledger, claims, export.

Shiprocket is MOCKED. The wallet-balance / statement / serviceability response
shapes used here are assumptions (they are not verified against a live
account); the parsers are tolerant and these tests pin the safety behaviour,
not Shiprocket's real field names.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from app.core.config import settings
from app.core.exceptions import IntegrationError, ValidationError
from app.integrations.registry import clear_adapters, register_adapter
from app.integrations.shiprocket.adapter import ShiprocketAdapter
from app.integrations.shiprocket.wallet import (
    extract_rate_estimate,
    normalize_wallet_transaction,
    parse_wallet_balance,
)
from app.models.enums import PaymentType
from app.models.order import Order
from app.models.shipment import Shipment
from app.models.shiprocket_wallet import (
    ShiprocketWalletCheck,
    ShiprocketWalletSnapshot,
    ShiprocketWalletTransaction,
)
from app.schemas.order import OrderItemCreateRequest
from app.services.order_service import OrderService
from app.services.shiprocket_service import ShiprocketOperationsService
from app.services.shiprocket_wallet_service import (
    AssignContext,
    WalletInsufficientError,
    WalletService,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio


class _Client:
    """Scripted Shiprocket client: path -> list of responses (or exceptions)."""

    def __init__(self, routes: dict[str, list]) -> None:
        self.routes = {k: list(v) for k, v in routes.items()}
        self.calls: list[tuple[str, str]] = []

    async def request(self, method, path, *, json=None, params=None):  # noqa: ANN001, ANN201
        self.calls.append((method, path))
        queue = self.routes.get(path)
        if not queue:
            raise IntegrationError("no route", details={"error_type": "http_404"})
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(item, Exception):
            raise item
        return item

    async def ensure_authenticated(self) -> None:
        pass


def _adapter(routes: dict[str, list]) -> tuple[ShiprocketAdapter, _Client]:
    client = _Client(routes)
    adapter = ShiprocketAdapter(client=client)  # type: ignore[arg-type]
    return adapter, client


BAL = "/account/details/wallet-balance"
SERV = "/courier/serviceability/"


def _serv(rate: float, rec: int = 1) -> dict:
    return {
        "data": {
            "recommended_courier_company_id": rec,
            "available_courier_companies": [
                {"courier_company_id": 1, "courier_name": "Delhivery", "rate": rate},
                {"courier_company_id": 2, "courier_name": "Bluedart", "rate": rate + 40},
            ],
        }
    }


@pytest.fixture(autouse=True)
def _enable_guard(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "SHIPROCKET_WALLET_GUARD_ENABLED", True)
    monkeypatch.setattr(settings, "SHIPROCKET_WALLET_SAFETY_BUFFER", 50.0)
    monkeypatch.setattr(settings, "SHIPROCKET_WALLET_FALLBACK_CHARGE", 0.0)
    monkeypatch.setattr(settings, "SHIPROCKET_WALLET_LOW_BALANCE_THRESHOLD", 500.0)
    yield
    clear_adapters()


# ----------------------------------------------------------------- parsers


def test_balance_parser_is_tolerant_and_never_defaults_to_zero() -> None:
    assert parse_wallet_balance({"data": {"balance_amount": "1,250.50"}}) == Decimal("1250.50")
    assert parse_wallet_balance({"balance": 99}) == Decimal("99")
    assert parse_wallet_balance({"data": {"something": 1}}) is None
    assert parse_wallet_balance({"balance": "n/a"}) is None
    assert parse_wallet_balance("oops") is None


def test_rate_estimate_prefers_named_courier_then_recommended_then_max() -> None:
    body = _serv(100, rec=1)
    assert extract_rate_estimate(body, courier_id="2")[0] == Decimal("140")
    assert extract_rate_estimate(body)[0] == Decimal("100")  # recommended
    body["data"].pop("recommended_courier_company_id")
    assert extract_rate_estimate(body)[0] == Decimal("140")  # conservative max
    assert extract_rate_estimate(body, courier_id="99") is None  # cannot price -> unknown
    assert extract_rate_estimate({"data": {}}) is None


def test_statement_rows_are_classified_and_never_guessed() -> None:
    def n(**kw):  # noqa: ANN003, ANN202
        base = {"date": "2026-11-01 10:00:00", "amount": 100, "transaction_id": "T1"}
        return normalize_wallet_transaction({**base, **kw})

    assert n(type="credit", description="Wallet Recharge via UPI")["txn_type"] == "recharge"
    assert n(type="debit", description="Freight charges for AWB1")["txn_type"] == "freight"
    assert n(type="debit", description="COD charges")["txn_type"] == "cod_charge"
    assert n(type="debit", description="RTO freight")["txn_type"] == "rto_freight"
    assert n(type="credit", description="Freight reversal")["txn_type"] == "freight_reversal"
    assert n(type="credit", description="COD charge reversal")["txn_type"] == "cod_reversal"
    assert n(type="credit", description="Refund for cancelled")["txn_type"] == "refund"
    assert (
        n(type="debit", description="Weight discrepancy charge")["txn_type"] == "weight_discrepancy"
    )
    assert n(type="credit", description="misc")["txn_type"] == "other_credit"
    assert n(type="debit", description="misc")["txn_type"] == "other_debit"
    # amount is stored positive; direction carries the sign
    assert normalize_wallet_transaction({"date": "2026-11-01", "amount": -40, "type": "debit"})[
        "amount"
    ] == Decimal("40")
    # missing direction / date / amount -> skipped, not guessed
    assert normalize_wallet_transaction({"date": "2026-11-01", "amount": 5}) is None
    assert normalize_wallet_transaction({"amount": 5, "type": "debit"}) is None
    assert normalize_wallet_transaction({"date": "2026-11-01", "type": "debit"}) is None


# ---------------------------------------------------------------- balance


async def test_refresh_stores_timestamped_snapshot_and_flags_low_balance(
    db_session: AsyncSession,
) -> None:
    adapter, _ = _adapter({BAL: [{"data": {"balance_amount": 120}}]})
    svc = WalletService(db_session)
    snap = await svc.refresh_balance(adapter, trigger="manual")
    assert snap.balance == Decimal("120") and snap.trigger == "manual"
    state = await svc.state()
    assert state.balance == Decimal("120") and state.low_balance and not state.stale
    assert state.ledger_sync_enabled is False


async def test_unreadable_balance_is_an_error_not_zero(db_session: AsyncSession) -> None:
    adapter, _ = _adapter({BAL: [{"data": {"weird": True}}]})
    with pytest.raises(IntegrationError):
        await WalletService(db_session).refresh_balance(adapter)
    assert (
        await db_session.execute(select(func.count()).select_from(ShiprocketWalletSnapshot))
    ).scalar() == 0


# ------------------------------------------------------------------ guard


async def test_guard_blocks_with_figures_when_balance_is_short(db_session: AsyncSession) -> None:
    adapter, _ = _adapter({BAL: [{"data": {"balance_amount": 100}}], SERV: [_serv(95)]})
    ctx = AssignContext(order_number="O1", shiprocket_order_id="9001")
    with pytest.raises(WalletInsufficientError) as exc:
        await WalletService(db_session).check_before_assignment(adapter, ctx)
    d = exc.value.details
    assert d["current_balance"] == "100.00" or d["current_balance"] == "100"
    assert Decimal(d["estimated_charge"]) == Decimal("95")
    assert Decimal(d["required_minimum"]) == Decimal("145")  # 95 + 50 buffer
    assert Decimal(d["recharge_needed"]) == Decimal("45")
    assert exc.value.status_code == 409
    check = (await db_session.execute(select(ShiprocketWalletCheck))).scalar_one()
    assert check.outcome == "blocked" and check.estimated_charge == Decimal("95")


async def test_guard_allows_when_funds_suffice_and_keeps_estimates_out_of_ledger(
    db_session: AsyncSession,
) -> None:
    adapter, _ = _adapter({BAL: [{"data": {"balance_amount": 1000}}], SERV: [_serv(95)]})
    await WalletService(db_session).check_before_assignment(
        adapter, AssignContext(order_number="O2", shiprocket_order_id="9002")
    )
    check = (await db_session.execute(select(ShiprocketWalletCheck))).scalar_one()
    assert check.outcome == "allowed" and check.shortfall is None
    # an estimate is never an actual wallet debit
    n = (
        await db_session.execute(select(func.count()).select_from(ShiprocketWalletTransaction))
    ).scalar()
    assert n == 0


async def test_guard_fails_open_when_balance_or_estimate_unreadable(
    db_session: AsyncSession,
) -> None:
    # balance endpoint errors -> allowed_unverified, never blocks on our own failure
    adapter, _ = _adapter({BAL: [IntegrationError("down")], SERV: [_serv(95)]})
    await WalletService(db_session).check_before_assignment(
        adapter, AssignContext(shiprocket_order_id="1")
    )
    # balance fine but no estimate -> only buffer applies; 60 >= 50 passes
    adapter2, _ = _adapter({BAL: [{"balance": 60}]})
    await WalletService(db_session).check_before_assignment(
        adapter2, AssignContext(shiprocket_order_id="1")
    )
    outcomes = sorted(
        o for (o,) in (await db_session.execute(select(ShiprocketWalletCheck.outcome))).all()
    )
    assert outcomes == ["allowed", "allowed_unverified"]


async def test_guard_can_be_disabled(db_session: AsyncSession, monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(settings, "SHIPROCKET_WALLET_GUARD_ENABLED", False)
    adapter, client = _adapter({})
    await WalletService(db_session).check_before_assignment(adapter, AssignContext())
    assert client.calls == []


# ------------------------------------------- integration with AWB assignment


async def _order_with_shipment(session: AsyncSession) -> Shipment:
    order = await OrderService(session).create_order(
        actor=None,
        order_number="W-1",
        customer_id=None,
        order_datetime=datetime.now(UTC),
        currency="INR",
        payment_type=PaymentType.PREPAID,
        shipping_charge=Decimal("0"),
        notes=None,
        items=[
            OrderItemCreateRequest(sku="S", product_name="P", quantity=1, unit_price=Decimal("499"))
        ],
    )
    order.shipping_address = {"pin_code": "400001"}
    shipment = Shipment(
        order_id=order.id,
        shiprocket_shipment_id="5001",
        raw_external_payload={"order_id": "9001"},
    )
    session.add(shipment)
    await session.commit()
    return shipment


async def test_blocked_assignment_makes_no_assign_call_and_changes_nothing(
    db_session: AsyncSession,
    monkeypatch,  # noqa: ANN001
) -> None:
    monkeypatch.setattr(settings, "SHIPROCKET_EMAIL", "a@b.c")
    monkeypatch.setattr(settings, "SHIPROCKET_PASSWORD", "x")
    shipment = await _order_with_shipment(db_session)
    adapter, client = _adapter(
        {
            BAL: [{"data": {"balance_amount": 10}}],
            SERV: [_serv(95)],
            "/courier/assign/awb": [{"response": {"data": {"awb_code": "AWB1"}}}],
        }
    )
    register_adapter(adapter)
    shipments_before = (
        await db_session.execute(select(func.count()).select_from(Shipment))
    ).scalar()
    orders_before = (await db_session.execute(select(func.count()).select_from(Order))).scalar()

    with pytest.raises(WalletInsufficientError) as exc:
        await ShiprocketOperationsService(db_session).assign_awb(
            shipment.id, actor=None, courier_id=None
        )

    assert "Recharge needed" in exc.value.message
    paths = [p for _, p in client.calls]
    assert "/courier/assign/awb" not in paths and "/orders/create/adhoc" not in paths
    await db_session.refresh(shipment)
    assert shipment.awb is None
    assert (
        await db_session.execute(select(func.count()).select_from(Shipment))
    ).scalar() == shipments_before
    assert (
        await db_session.execute(select(func.count()).select_from(Order))
    ).scalar() == orders_before


async def test_sufficient_balance_assigns_exactly_once(
    db_session: AsyncSession,
    monkeypatch,  # noqa: ANN001
) -> None:
    monkeypatch.setattr(settings, "SHIPROCKET_EMAIL", "a@b.c")
    monkeypatch.setattr(settings, "SHIPROCKET_PASSWORD", "x")
    shipment = await _order_with_shipment(db_session)
    adapter, client = _adapter(
        {
            BAL: [{"data": {"balance_amount": 5000}}],
            SERV: [_serv(95)],
            "/courier/assign/awb": [
                {
                    "response": {
                        "data": {
                            "awb_code": "AWB1",
                            "courier_name": "Delhivery",
                            "courier_company_id": "1",
                        }
                    }
                }
            ],
        }
    )
    register_adapter(adapter)
    updated = await ShiprocketOperationsService(db_session).assign_awb(
        shipment.id, actor=None, courier_id=None
    )
    assert updated.awb == "AWB1"
    assert [p for _, p in client.calls].count("/courier/assign/awb") == 1


# ---------------------------------------------------------------- ledger


def _row(ref: str, kind: str, amount: float, desc: str, awb: str | None = None, **kw) -> dict:  # noqa: ANN003
    return {
        "transaction_id": ref,
        "date": "2026-11-05 09:00:00",
        "amount": amount,
        "type": kind,
        "description": desc,
        "awb_code": awb,
        "order_id": kw.get("order"),
        "courier_name": kw.get("courier"),
        "balance": kw.get("bal"),
    }


class _StmtAdapter:
    def __init__(self, pages: list[list[dict]]) -> None:
        self.pages = pages

    async def fetch_wallet_statement(self, *, page: int = 1, per_page: int = 100):  # noqa: ANN201
        return self.pages[page - 1], page < len(self.pages)


async def test_sync_deduplicates_records_reversals_and_preserves_verified_rows(
    db_session: AsyncSession,
) -> None:
    rows = [
        _row("T1", "credit", 1000, "Wallet Recharge", bal=1000),
        _row(
            "T2",
            "debit",
            100,
            "Freight charges",
            awb="A1",
            order="O1",
            courier="Delhivery",
            bal=900,
        ),
        _row("T3", "debit", 30, "COD charges", awb="A1", order="O1", courier="Delhivery", bal=870),
        _row("T4", "debit", 90, "RTO freight", awb="A2", order="O2", courier="Delhivery", bal=780),
        _row("T5", "credit", 30, "COD charge reversal", awb="A1", order="O1", bal=810),
        _row("T6", "credit", 20, "Freight reversal", awb="A1", order="O1", bal=830),
    ]
    svc = WalletService(db_session)
    first = await svc.sync_transactions(_StmtAdapter([rows[:3], rows[3:]]))  # type: ignore[arg-type]
    assert first == {"created": 6, "updated": 0, "skipped": 0, "unparsed": 0}

    # a human marks T2 verified; a later sync must not overwrite it
    t2 = (
        await db_session.execute(
            select(ShiprocketWalletTransaction).where(
                ShiprocketWalletTransaction.transaction_ref == "T2"
            )
        )
    ).scalar_one()
    t2.manually_verified = True
    t2.description = "verified by finance"
    await db_session.commit()

    changed = [dict(r) for r in rows]
    changed[1]["description"] = "Freight charges (edited upstream)"
    second = await svc.sync_transactions(_StmtAdapter([changed]))  # type: ignore[arg-type]
    assert second["created"] == 0 and second["skipped"] == 1 and second["updated"] == 5

    n = (
        await db_session.execute(select(func.count()).select_from(ShiprocketWalletTransaction))
    ).scalar()
    assert n == 6  # no duplicates
    await db_session.refresh(t2)
    assert t2.description == "verified by finance"

    s = await svc.summary(date_from=None, date_to=None)
    assert s.total_recharges == 1000
    assert s.shipping_expenses == 100 and s.cod_charges == 30 and s.rto_expenses == 90
    assert s.credits_and_reversals == 50  # 30 COD reversal + 20 freight reversal
    assert s.net_shipping_cost == 170  # 220 expenses - 50 reversed
    assert s.closing_balance is not None


async def test_ledger_consistency_is_reported_only_when_balances_are_known(
    db_session: AsyncSession,
) -> None:
    svc = WalletService(db_session)
    await svc.sync_transactions(
        _StmtAdapter(
            [
                [  # type: ignore[arg-type]
                    _row("A", "credit", 500, "Wallet Recharge", bal=500),
                    _row("B", "debit", 100, "Freight", bal=400),
                ]
            ]
        )
    )
    # opening balance for a window starting after the rows comes from reported rows only
    later = await svc.summary(date_from=datetime(2026, 11, 6, tzinfo=UTC), date_to=None)
    assert later.opening_balance in (Decimal("400"), Decimal("500"))  # tie on timestamp
    assert later.closing_balance in (Decimal("400"), Decimal("500"))
    full = await svc.summary(date_from=None, date_to=None)
    assert full.opening_balance is None and full.ledger_consistent is None  # unknown, not faked


async def test_sync_failures_are_loud_and_store_nothing(db_session: AsyncSession) -> None:
    svc = WalletService(db_session)
    with pytest.raises(ValidationError):  # rows present but none parseable
        await svc.sync_transactions(_StmtAdapter([[{"foo": 1}, {"bar": 2}]]))  # type: ignore[arg-type]
    assert (
        await db_session.execute(select(func.count()).select_from(ShiprocketWalletTransaction))
    ).scalar() == 0
    # statement path unset -> adapter refuses (no guessed endpoint)
    adapter, client = _adapter({})
    with pytest.raises(IntegrationError):
        await adapter.fetch_wallet_statement()
    assert client.calls == []


# --------------------------------------------------------------- recharges


async def test_manual_recharge_claim_is_not_counted_until_balance_proves_it(
    db_session: AsyncSession,
) -> None:
    svc = WalletService(db_session)
    pre, _ = _adapter({BAL: [{"data": {"balance_amount": 200}}]})
    await svc.refresh_balance(pre)
    claim = await svc.create_recharge_claim(
        amount=Decimal("1000"), payment_reference="UPI-123", note=None, user_id=None
    )
    assert claim.status == "unverified"
    assert (await svc.summary(date_from=None, date_to=None)).total_recharges == 0

    # balance did NOT rise by 1000 -> stays unverified
    flat, _ = _adapter({BAL: [{"data": {"balance_amount": 250}}]})
    with pytest.raises(ValidationError):
        await svc.confirm_claim_by_balance(flat, claim.id, user_id=None)
    assert (await svc.summary(date_from=None, date_to=None)).total_recharges == 0
    assert (await svc.summary(date_from=None, date_to=None)).unverified_recharge_claims == 1

    risen, _ = _adapter({BAL: [{"data": {"balance_amount": 1250}}]})
    done = await svc.confirm_claim_by_balance(risen, claim.id, user_id=None)
    assert done.status == "confirmed" and done.source == "balance_confirmed"
    assert (await svc.summary(date_from=None, date_to=None)).total_recharges == 1000

    # when Shiprocket's own statement later reports it, the claim is superseded (no double count)
    await svc.sync_transactions(_StmtAdapter([[_row("R1", "credit", 1000, "Wallet Recharge")]]))  # type: ignore[arg-type]
    assert (await svc.summary(date_from=None, date_to=None)).total_recharges == 1000


# ---------------------------------------------------- listing/export/HTTP


async def test_list_filters_order_expenses_periods_and_export(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    svc = WalletService(db_session)
    await svc.sync_transactions(
        _StmtAdapter(
            [
                [  # type: ignore[arg-type]
                    _row(
                        "T1",
                        "debit",
                        100,
                        "Freight charges",
                        awb="A1",
                        order="O1",
                        courier="Delhivery",
                    ),
                    _row(
                        "T2",
                        "credit",
                        20,
                        "Freight reversal",
                        awb="A1",
                        order="O1",
                        courier="Delhivery",
                    ),
                    _row(
                        "T3",
                        "debit",
                        80,
                        "Freight charges",
                        awb="A9",
                        order="O9",
                        courier="Bluedart",
                    ),
                ]
            ]
        )
    )
    rows, total = await svc.list_transactions(
        page=1,
        page_size=10,
        q="A1",
        date_from=None,
        date_to=None,
        txn_type=None,
        direction=None,
        courier=None,
        status=None,
    )
    assert total == 2
    rows, total = await svc.list_transactions(
        page=1,
        page_size=10,
        q=None,
        date_from=None,
        date_to=None,
        txn_type=None,
        direction="debit",
        courier="blue",
        status=None,
    )
    assert total == 1 and rows[0].awb == "A9"

    oe = await svc.order_expenses(order_number="O1", awb=None)
    assert oe["actual"]["total_debits"] == 100 and oe["actual"]["net_cost"] == 80
    assert oe["estimated"] == []  # estimates are reported separately, never mixed in

    months = await svc.monthly_summary(date_from=None, date_to=None, by="month")
    assert months[0]["period"] == "2026-11" and months[0]["net_shipping_cost"] == 160
    assert (await svc.monthly_summary(date_from=None, date_to=None, by="year"))[0][
        "period"
    ] == "2026"

    async with await make_authenticated_client(
        db_session, permission_codes=["analytics.read"], email="w@example.com"
    ) as c:
        assert (await c.get("/api/v1/shiprocket-wallet/state")).status_code == 200
        assert (await c.get("/api/v1/shiprocket-wallet/summary")).status_code == 200
        r = await c.get("/api/v1/shiprocket-wallet/transactions?direction=debit")
        assert r.status_code == 200 and r.json()["meta"]["total_items"] == 2
        x = await c.get("/api/v1/shiprocket-wallet/transactions/export")
        assert x.status_code == 200 and x.content[:2] == b"PK"
        assert (
            await c.get("/api/v1/shiprocket-wallet/order-expenses?order_number=O1")
        ).status_code == 200
        # viewing does not allow sync, claims or probe
        assert (await c.post("/api/v1/shiprocket-wallet/sync")).status_code == 403
        assert (
            await c.post(
                "/api/v1/shiprocket-wallet/recharge-claims",
                json={"amount": "5", "payment_reference": "x"},
            )
        ).status_code == 403
        assert (await c.get("/api/v1/shiprocket-wallet/probe")).status_code == 403


async def test_probes_are_superuser_only_even_with_manage_permission(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    async with await make_authenticated_client(
        db_session, permission_codes=["reconciliation.manage"], email="m@example.com"
    ) as c:
        for path in (
            "/api/v1/shiprocket-wallet/probe",
            "/api/v1/revenue-reconciliation/shiprocket-probe",
        ):
            assert (await c.get(path)).status_code == 403


def test_wallet_guard_defaults_off_until_shiprocket_formats_are_verified() -> None:
    from app.core.config import Settings

    assert Settings.model_fields["SHIPROCKET_WALLET_GUARD_ENABLED"].default is False
    assert Settings.model_fields["SHIPROCKET_WALLET_STATEMENT_PATH"].default == ""
    assert Settings.model_fields["SHIPROCKET_REMITTANCE_PATH"].default == ""

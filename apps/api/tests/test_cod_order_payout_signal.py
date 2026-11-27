"""Shiprocket's documented order-detail payout fields -> Payment Initiated.

Source of truth: Shiprocket's published API collection (apidocs.shiprocket.in),
`GET /v1/external/orders/show/{id}`: `data.remittance_utr`, `data.remittance_date`,
`data.remittance_status` (blank strings in the documented example), plus `data.cod`,
`data.payment_method`, `data.payment_status`. Nothing documents a remitted amount, a
CRF id, a status vocabulary, or a bank settlement -- so these fields can make an order
"Payment Initiated" (a payout reference / date was reported) and can NEVER make it
"Payment Success". Synthetic data only.
"""

from __future__ import annotations

import pytest
from app.models.cod_remittance import CodRemittance
from app.services.shiprocket_finance_service import ShiprocketFinanceService
from sqlalchemy import select

from tests.test_shiprocket_finance import ADMIN, RECON, FakeShiprocket, _client, _shipped

pytestmark = pytest.mark.asyncio

PARAMS = {"date_from": "2020-01-01T00:00:00Z", "date_to": "2099-01-01T00:00:00Z"}


def docs_order_body(**remittance):
    """Shaped like the documented `orders/show` success example: remittance fields are
    present and blank by default; unrelated customer data must never be stored."""
    data = {
        "id": 16167171,
        "channel_order_id": "OMS-1",
        "payment_method": "cod",
        "payment_status": "",
        "cod": "1000",
        "status_code": "7",
        "delivered_date": None,
        "awb_data": {"charges": {"cod_charges": "", "freight_charges": ""}},
        "remittance_date": "",
        "remittance_utr": "",
        "remittance_status": "",
        "customer_name": "must never be stored",
    }
    data.update(remittance)
    return {"data": data}


async def _kpis(client):
    return (await client.get(f"{RECON}/cod/summary", params=PARAMS)).json()["data"]


async def _sync(db, body, order_id="9001"):
    await ShiprocketFinanceService(db).sync_order_finance(
        FakeShiprocket({order_id: body}), limit=5, user=None
    )


async def _only(db, **extra):
    await _shipped(db, "PO-1", "AWB-PO1", "9001")
    return db


async def test_blank_documented_remittance_fields_stay_pending(db_session):
    await _only(db_session)
    await _sync(db_session, docs_order_body())  # the documented example: all blank
    async with await _client(db_session, ADMIN) as client:
        data = await _kpis(client)
    k = data["kpis"]
    assert (k["payment_success"]["count"], k["payment_initiated"]["count"]) == (0, 0)
    assert k["payment_pending"]["count"] == 1
    assert data["data_coverage"]["order_payout_references"] == 0
    assert data["data_coverage"]["pending_is_data_limited"] is True


async def test_a_payout_utr_makes_the_order_initiated_never_success(db_session):
    await _only(db_session)
    await _sync(
        db_session,
        docs_order_body(
            remittance_utr="UTR123", remittance_date="2026-11-20", remittance_status="Paid"
        ),
    )
    async with await _client(db_session, ADMIN) as client:
        data = await _kpis(client)
        k = data["kpis"]
        assert (k["payment_success"]["count"], k["payment_initiated"]["count"]) == (0, 1)
        assert k["payment_pending"]["count"] == 0
        # The reconciliation status is untouched: no amount was reported.
        assert k["pending"]["count"] == 1 and k["short"]["count"] == 0
        assert k["remittance_received"]["count"] == 0
        cov = data["data_coverage"]
        assert cov["order_payout_references"] == 1 and cov["has_remittance_data"] is True
        assert cov["pending_is_data_limited"] is False
        rows = (
            await client.get(
                f"{RECON}/cod/records",
                params={**PARAMS, "kpi": "payment_initiated", "page_size": 50},
            )
        ).json()
    assert rows["meta"]["total_items"] == 1
    row = rows["data"][0]
    assert row["payout_reported"] is True and row["source"] == "shiprocket_order"
    assert row["status"] == "pending" and row["remittance_reference"] == "UTR123"
    assert row["remittance_amount"] in ("0", "0.00")  # no amount was invented


async def test_remittance_date_alone_counts_as_reported(db_session):
    await _only(db_session)
    await _sync(db_session, docs_order_body(remittance_date="2026-11-20"))
    async with await _client(db_session, ADMIN) as client:
        k = (await _kpis(client))["kpis"]
    assert k["payment_initiated"]["count"] == 1 and k["payment_pending"]["count"] == 0


async def test_status_text_alone_is_not_used_because_its_values_are_undocumented(db_session):
    await _only(db_session)
    await _sync(db_session, docs_order_body(remittance_status="Paid"))
    async with await _client(db_session, ADMIN) as client:
        k = (await _kpis(client))["kpis"]
    assert k["payment_initiated"]["count"] == 0 and k["payment_pending"]["count"] == 1


@pytest.mark.parametrize(
    "remittance",
    [
        {"remittance_utr": "NA", "remittance_date": "N/A", "remittance_status": ""},
        {"remittance_utr": None, "remittance_date": None, "remittance_status": None},
    ],
)
async def test_na_and_null_remittance_values_are_missing_not_reported(db_session, remittance):
    await _only(db_session)
    await _sync(db_session, docs_order_body(**remittance))
    async with await _client(db_session, ADMIN) as client:
        k = (await _kpis(client))["kpis"]
    assert k["payment_initiated"]["count"] == 0 and k["payment_pending"]["count"] == 1


async def test_response_without_remittance_keys_or_an_error_body_is_handled(db_session):
    await _only(db_session)
    body = docs_order_body()
    for key in ("remittance_utr", "remittance_date", "remittance_status"):
        body["data"].pop(key)  # keys absent entirely
    await _sync(db_session, body)
    # A body that is not an order at all (e.g. the documented 404 / 400 shape).
    await _sync(db_session, {"message": "Order not found", "status_code": 404})
    async with await _client(db_session, ADMIN) as client:
        k = (await _kpis(client))["kpis"]
    assert k["payment_initiated"]["count"] == 0 and k["payment_pending"]["count"] == 1
    assert (await db_session.execute(select(CodRemittance))).scalars().all() == []


async def test_order_level_signal_never_reaches_success_even_with_every_field_filled(db_session):
    await _only(db_session)
    await _sync(
        db_session,
        docs_order_body(remittance_utr="U", remittance_date="2026-11-20", remittance_status="Paid"),
    )
    async with await _client(db_session, ADMIN) as client:
        k = (await _kpis(client))["kpis"]
    assert k["payment_success"]["count"] == 0
    assert k["bank_confirmed"]["count"] == 0


async def test_success_initiated_pending_partition_the_delivered_orders(db_session):
    await _shipped(db_session, "PA-1", "AWB-PA1", "9001")  # payout reported
    await _shipped(db_session, "PA-2", "AWB-PA2", "9002")  # blank -> pending
    await _shipped(db_session, "PA-3", "AWB-PA3", "9003")  # no Shiprocket id known -> pending
    await ShiprocketFinanceService(db_session).sync_order_finance(
        FakeShiprocket(
            {
                "9001": docs_order_body(remittance_utr="U1"),
                "9002": docs_order_body(),
                "9003": docs_order_body(),
            }
        ),
        limit=10,
        user=None,
    )
    async with await _client(db_session, ADMIN) as client:
        k = (await _kpis(client))["kpis"]
        total = k["delivered_orders"]["count"]
        counts = {
            n: k[n]["count"] for n in ("payment_success", "payment_initiated", "payment_pending")
        }
        assert sum(counts.values()) == total == 3
        assert counts == {"payment_success": 0, "payment_initiated": 1, "payment_pending": 2}
        seen: set[str] = set()
        for kpi, expected in counts.items():
            body = (
                await client.get(
                    f"{RECON}/cod/records", params={**PARAMS, "kpi": kpi, "page_size": 50}
                )
            ).json()
            ids = {r["order_id"] for r in body["data"]}
            assert len(ids) == expected == body["meta"]["total_items"], kpi
            assert not (ids & seen), f"{kpi} overlaps"
            seen |= ids


@pytest.mark.parametrize(
    "stamp", ["20-11-2026 10:15", "20/11/2026", "Nov 20, 2026", "2026-11-20 10:15:00"]
)
async def test_a_date_in_a_common_format_is_not_lost(db_session, stamp):
    await _only(db_session)
    await _sync(db_session, docs_order_body(remittance_date=stamp))
    async with await _client(db_session, ADMIN) as client:
        k = (await _kpis(client))["kpis"]
    assert k["payment_initiated"]["count"] == 1 and k["payment_pending"]["count"] == 0

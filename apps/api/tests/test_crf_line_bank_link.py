"""Line-level bank verification of Shiprocket CRF lines (no UTR).

CRF `processed` entries are stored as `CodRemittanceLine` rows carrying only
awb / amount. A bank credit pays one only through an explicit, audited,
candidate-checked link; AWB + amount are candidate evidence, never proof.
Synthetic data only.
"""

from __future__ import annotations

import hashlib
from decimal import Decimal
from types import SimpleNamespace

import pytest
from app.main import app
from app.models.audit_log import AuditLog
from app.models.remittance_reconciliation import BankCredit, BankCreditLink, CodRemittanceLine
from app.services.cod_reconciliation_service import CodReconciliationService
from sqlalchemy import select

from tests.test_remittance_drilldowns import BASE, _admin, _client
from tests.test_remittance_manual_match_and_uploads import _dp, _order

pytestmark = pytest.mark.asyncio
D = Decimal


@pytest.fixture(autouse=True)
def _clear_override():
    yield
    app.dependency_overrides.clear()


class _Adapter:
    def __init__(self, records: list[dict]) -> None:
        self.records = records

    async def fetch_cod_remittances(self, *, page: int = 1, limit: int = 100):
        return (self.records if page == 1 else []), False


async def _sync(db, *entries: tuple[str, int, str]) -> None:
    records = [{"awb": a, "amount": amt, "status": st} for a, amt, st in entries]
    await CodReconciliationService(db).sync_remittances(_Adapter(records))


async def _credit(db, amount: str, narration: str, key: str) -> SimpleNamespace:
    credit = BankCredit(
        amount=D(amount),
        narration=narration,
        reference=f"BNK-{key}",
        dedupe_key=hashlib.sha256(key.encode()).hexdigest(),
    )
    db.add(credit)
    await db.commit()
    # Plain snapshot: a failed link rolls the session back and expires ORM objects.
    return SimpleNamespace(id=credit.id, reference=credit.reference)


async def _line(db, awb: str, amount: str) -> SimpleNamespace:
    row = (
        await db.execute(
            select(CodRemittanceLine).where(
                CodRemittanceLine.awb == awb, CodRemittanceLine.remittance_amount == D(amount)
            )
        )
    ).scalar_one()
    return SimpleNamespace(id=row.id)


async def _kpis(client) -> dict:
    res = await client.get(f"{BASE}/cod/summary", params=_dp())
    assert res.status_code == 200, res.text
    return res.json()["data"]["kpis"]


def _amt(kpis: dict, key: str) -> D:
    return D(str(kpis[key]["amount"]))


async def _link(client, line, credit, amount, *, confirm=False, reason="Checked statement"):
    return await client.post(
        f"{BASE}/cod/crf-lines/{line.id}/bank-links",
        json={
            "bank_credit_id": str(credit.id),
            "bank_reference": credit.reference,
            "amount": str(amount),
            "reason": reason,
            "confirm_ambiguous": confirm,
        },
    )


async def test_unique_match_moves_initiated_to_success_with_audit(db_session):
    await _order(db_session, "CRF1", "1000", awb="AWB-U1")
    await _sync(db_session, ("AWB-U1", 1000, "processed"))
    credit = await _credit(db_session, "1000", "NEFT SHIPROCKET AWB-U1", "u1")
    line = await _line(db_session, "AWB-U1", "1000")
    admin = await _admin(db_session)
    async with _client(db_session, admin) as client:
        before = await _kpis(client)
        # Courier says processed; with no bank evidence that is only Initiated.
        assert _amt(before, "payment_initiated") == D("1000")
        assert _amt(before, "payment_success") == D("0")

        cand = await client.get(f"{BASE}/cod/crf-lines/{line.id}/bank-candidates")
        data = cand.json()["data"]
        assert [c["bank_credit_id"] for c in data["candidates"]] == [str(credit.id)]
        assert data["requires_explicit_confirmation"] is False
        # Candidate listing alone never changes anything.
        assert _amt(await _kpis(client), "payment_success") == D("0")

        res = await _link(client, line, credit, "1000")
        assert res.status_code == 201, res.text
        after = await _kpis(client)
        assert _amt(after, "payment_success") == D("1000")
        assert _amt(after, "payment_initiated") == D("0")

    actions = set((await db_session.execute(select(AuditLog.action))).scalars())
    assert {"remittance.cod.crf_line_reference_assigned", "remittance.cod.bank_link"} <= actions
    link = (await db_session.execute(select(BankCreditLink))).scalar_one()
    assert link.method == "manual" and link.linked_by == admin.id and link.note


async def test_ambiguous_match_requires_explicit_confirmation(db_session):
    await _order(db_session, "CRF2", "1000", awb="AWB-A1")
    await _sync(db_session, ("AWB-A1", 1000, "processed"))
    c1 = await _credit(db_session, "1000", "IMPS AWB-A1 batch 1", "a1")
    await _credit(db_session, "1000", "IMPS AWB-A1 batch 2", "a2")
    line = await _line(db_session, "AWB-A1", "1000")
    admin = await _admin(db_session)
    async with _client(db_session, admin) as client:
        cand = (await client.get(f"{BASE}/cod/crf-lines/{line.id}/bank-candidates")).json()["data"]
        assert len(cand["candidates"]) == 2 and cand["requires_explicit_confirmation"] is True

        refused = await _link(client, line, c1, "1000")
        assert refused.status_code >= 400
        assert (await db_session.execute(select(BankCreditLink))).first() is None
        assert _amt(await _kpis(client), "payment_success") == D("0")

        ok = await _link(client, line, c1, "1000", confirm=True)
        assert ok.status_code == 201, ok.text
        assert _amt(await _kpis(client), "payment_success") == D("1000")


async def test_non_candidate_credit_is_rejected(db_session):
    await _order(db_session, "CRF3", "1000", awb="AWB-N1")
    await _sync(db_session, ("AWB-N1", 1000, "processed"))
    stray = await _credit(db_session, "750", "UNRELATED CREDIT", "n1")
    line = await _line(db_session, "AWB-N1", "1000")
    async with _client(db_session, await _admin(db_session)) as client:
        res = await _link(client, line, stray, "750")
        assert res.status_code >= 400
        assert (await db_session.execute(select(BankCreditLink))).first() is None
        assert (await db_session.get(CodRemittanceLine, line.id)).remittance_reference is None


async def test_partial_credits_and_multiple_crf_entries_per_awb(db_session):
    await _order(db_session, "CRF4", "1000", awb="AWB-P1")
    # Two distinct processed CRF transactions for one AWB (partial payouts).
    await _sync(db_session, ("AWB-P1", 400, "processed"), ("AWB-P1", 600, "processed"))
    first = await _line(db_session, "AWB-P1", "400")
    second = await _line(db_session, "AWB-P1", "600")
    assert first.id != second.id
    c1 = await _credit(db_session, "400", "NEFT AWB-P1 part 1", "p1")
    c2 = await _credit(db_session, "600", "NEFT AWB-P1 part 2", "p2")
    async with _client(db_session, await _admin(db_session)) as client:
        # Both credits carry the AWB, so the first choice is ambiguous: explicit confirm.
        assert (await _link(client, first, c1, "400")).status_code >= 400
        assert (await _link(client, first, c1, "400", confirm=True)).status_code == 201
        mid = await _kpis(client)
        # Only 400 of 1000 bank-confirmed: not Success yet.
        assert _amt(mid, "payment_success") == D("0")
        assert _amt(mid, "payment_initiated") == D("1000")

        assert (await _link(client, second, c2, "600")).status_code == 201
        assert _amt(await _kpis(client), "payment_success") == D("1000")

    refs = {
        (await db_session.get(CodRemittanceLine, ln.id)).remittance_reference
        for ln in (first, second)
    }
    assert len(refs) == 2 and all(r.startswith("CRF-") for r in refs)


async def test_partial_bank_credit_for_one_line_stays_initiated(db_session):
    await _order(db_session, "CRF5", "1000", awb="AWB-Q1")
    await _sync(db_session, ("AWB-Q1", 1000, "processed"))
    credit = await _credit(db_session, "1000", "NEFT AWB-Q1", "q1")
    line = await _line(db_session, "AWB-Q1", "1000")
    async with _client(db_session, await _admin(db_session)) as client:
        assert (await _link(client, line, credit, "300")).status_code == 201
        kpis = await _kpis(client)
        assert _amt(kpis, "payment_success") == D("0")
        assert _amt(kpis, "payment_initiated") == D("1000")


async def test_duplicate_and_over_allocation_are_rejected(db_session):
    await _order(db_session, "CRF6", "1000", awb="AWB-D1")
    await _order(db_session, "CRF7", "1000", awb="AWB-D2")
    await _sync(db_session, ("AWB-D1", 1000, "processed"), ("AWB-D2", 1000, "processed"))
    credit = await _credit(db_session, "1000", "NEFT AWB-D1 AWB-D2", "d1")
    big = await _credit(db_session, "1500", "NEFT AWB-D1 big", "d2")
    line1 = await _line(db_session, "AWB-D1", "1000")
    line2 = await _line(db_session, "AWB-D2", "1000")
    async with _client(db_session, await _admin(db_session)) as client:
        # More than the line still needs.
        assert (await _link(client, line1, big, "1200", confirm=True)).status_code >= 400
        assert (await _link(client, line1, credit, "600", confirm=True)).status_code == 201
        # Same credit + same line again.
        assert (await _link(client, line1, credit, "100", confirm=True)).status_code >= 400
        # The other line cannot take the 400 left plus more than is free.
        assert (await _link(client, line2, credit, "500", confirm=True)).status_code >= 400
        assert (await _link(client, line2, credit, "400", confirm=True)).status_code == 201

    links = (await db_session.execute(select(BankCreditLink))).scalars().all()
    used = sum((D(str(x.amount)) for x in links if x.bank_credit_id == credit.id), D(0))
    assert used == D("1000")  # never more than the credit
    assert all(x.bank_credit_id == credit.id for x in links)

"""Cashfree "Sync Now" as a queued Celery task. The broker and Cashfree are
faked: no network call and no real credentials.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

import pytest
from app.api.v1.endpoints import remittance_reconciliation as endpoint
from app.models.audit_log import AuditLog
from app.models.remittance_reconciliation import GatewaySyncState
from app.services.cashfree_gateway_sync_service import (
    QUEUE_TTL,
    CashfreeGatewaySyncService,
)
from app.tasks import cashfree_gateway_sync as task_module
from app.tasks.cashfree_gateway_sync import sync_cashfree_manual_task
from app.workers.celery_app import DEFAULT_QUEUE, celery_app, route_task
from sqlalchemy import select

from tests.test_cashfree_gateway_sync import (
    BASE,
    CLIENT_ID,
    SECRET,
    FakeCashfree,
    _cashfree_configured,  # noqa: F401 - autouse fixture: fake credentials
    _client,
    _fake_queue,
    _work,
    lines,
    lot_row,
    recon_row,
)

UPLOADER = ["reconciliation.read", "reconciliation.upload"]


async def _status(client) -> dict:
    return (await client.get(f"{BASE}/cashfree/status")).json()["data"]


async def _state(db) -> GatewaySyncState:
    state = (await db.execute(select(GatewaySyncState))).scalar_one()
    await db.refresh(state)
    return state


@pytest.mark.asyncio
async def test_sync_now_only_enqueues_and_returns_at_once(db_session, monkeypatch):
    cashfree = FakeCashfree(monkeypatch)
    published = _fake_queue(monkeypatch)
    async with await _client(db_session, UPLOADER) as client:
        res = await client.post(f"{BASE}/cashfree/sync")
        assert res.status_code == 200, res.text
        assert res.json()["data"]["status"] == "queued"
        assert cashfree.calls == []  # nothing ran inside the API request
        status = await _status(client)
    assert status["queued"] is True and status["running"] is False
    assert status["last_sync_status"] == "queued" and status["last_attempt_at"] is None
    (message,) = published
    assert message["retry"] is False
    assert message["expires"] == int(QUEUE_TTL.total_seconds())
    assert SECRET not in str(message) and CLIENT_ID not in str(message)


@pytest.mark.asyncio
async def test_repeated_clicks_queue_one_task(db_session, monkeypatch):
    FakeCashfree(monkeypatch)
    published = _fake_queue(monkeypatch)
    async with await _client(db_session, UPLOADER) as client:
        outcomes = [
            (await client.post(f"{BASE}/cashfree/sync")).json()["data"]["status"] for _ in range(3)
        ]
    assert outcomes == ["queued", "already_queued", "already_queued"]
    assert len(published) == 1


@pytest.mark.asyncio
async def test_a_running_sync_is_not_queued_again(db_session, monkeypatch):
    FakeCashfree(monkeypatch)
    published = _fake_queue(monkeypatch)
    assert await CashfreeGatewaySyncService(db_session)._acquire_lock()
    async with await _client(db_session, UPLOADER) as client:
        res = await client.post(f"{BASE}/cashfree/sync")
    assert res.json()["data"]["status"] == "already_running" and published == []


@pytest.mark.asyncio
async def test_without_upload_permission_nothing_is_queued(db_session, monkeypatch):
    published = _fake_queue(monkeypatch)
    for index, perms in enumerate((["reconciliation.read"], ["orders.read"])):
        async with await _client(db_session, perms, f"no{index}@x.com") as client:
            assert (await client.post(f"{BASE}/cashfree/sync")).status_code == 403
    assert published == []


@pytest.mark.asyncio
async def test_worker_records_status_and_the_user_and_a_rerun_is_idempotent(
    db_session, monkeypatch
):
    FakeCashfree(
        monkeypatch,
        lines=[[recon_row(event_id="E1", order_id="AWL1", cf_payment_id="P1")]],
        lots=[[lot_row()]],
    )
    published = _fake_queue(monkeypatch)
    async with await _client(db_session, UPLOADER) as client:
        await client.post(f"{BASE}/cashfree/sync")
        assert await _work(db_session, published) == "ok"
        status = await _status(client)
        assert status["queued"] is False and status["running"] is False
        assert status["last_sync_status"] == "ok" and status["last_sync_trigger"] == "manual"
        assert status["last_successful_sync_at"] is not None
        first = len(await lines(db_session))
        assert await _work(db_session, published) == "ok"  # redelivery of the same message
        assert len(await lines(db_session)) == first == 1
        again = await client.post(f"{BASE}/cashfree/sync")
    assert again.json()["data"]["status"] == "queued"  # free to queue after completion
    log = (
        await db_session.execute(
            select(AuditLog).where(AuditLog.action == "remittance.prepaid.cashfree_sync").limit(1)
        )
    ).scalar_one()
    assert str(log.user_id) == published[0]["args"][0]


@pytest.mark.asyncio
async def test_requested_date_window_reaches_the_worker(db_session, monkeypatch):
    FakeCashfree(monkeypatch)
    published = _fake_queue(monkeypatch)
    async with await _client(db_session, UPLOADER) as client:
        await client.post(
            f"{BASE}/cashfree/sync",
            json={"date_from": "2026-11-01T00:00:00Z", "date_to": "2026-11-05T00:00:00Z"},
        )
    await _work(db_session, published)
    window = (await _state(db_session)).last_result
    assert window["window_from"].startswith("2026-11-01")
    assert window["window_to"].startswith("2026-11-05")


@pytest.mark.parametrize("failure", ["error", "timeout"])
@pytest.mark.asyncio
async def test_unreachable_broker_fails_fast_and_is_recorded(db_session, monkeypatch, failure):
    FakeCashfree(monkeypatch)

    def broken(**_kwargs):
        if failure == "timeout":
            time.sleep(1.0)
        else:
            raise ConnectionError(f"redis://user:{SECRET}@broker refused")

    monkeypatch.setattr(sync_cashfree_manual_task, "apply_async", broken)
    monkeypatch.setattr(endpoint, "ENQUEUE_TIMEOUT_SECONDS", 0.2)
    async with await _client(db_session, UPLOADER) as client:
        res = await client.post(f"{BASE}/cashfree/sync")
        assert res.status_code == 503, res.text
        assert SECRET not in res.text
        status = await _status(client)
        assert status["queued"] is False and status["connection_status"] == "error"
        assert "Could not queue the sync" in status["last_sync_error"]
        assert SECRET not in status["last_sync_error"]
        published = _fake_queue(monkeypatch)
        retry = await client.post(f"{BASE}/cashfree/sync")
    assert retry.json()["data"]["status"] == "queued" and len(published) == 1


@pytest.mark.asyncio
async def test_a_message_no_worker_took_shows_as_failed_and_can_be_requeued(
    db_session, monkeypatch
):
    FakeCashfree(monkeypatch)
    published = _fake_queue(monkeypatch)
    async with await _client(db_session, UPLOADER) as client:
        await client.post(f"{BASE}/cashfree/sync")
        state = await _state(db_session)
        old = datetime.now(UTC) - QUEUE_TTL - timedelta(seconds=5)
        state.last_status = f"queued:{int(old.timestamp())}"
        await db_session.commit()
        status = await _status(client)
        assert status["queued"] is False and status["last_sync_status"] == "failed"
        assert status["connection_status"] == "error"
        assert "no Celery worker" in status["last_sync_error"]
        res = await client.post(f"{BASE}/cashfree/sync")
    assert res.json()["data"]["status"] == "queued" and len(published) == 2


@pytest.mark.asyncio
async def test_worker_crash_releases_the_lock_and_records_the_failure(db_session, monkeypatch):
    FakeCashfree(monkeypatch)
    published = _fake_queue(monkeypatch)

    async def crash(self, **_kwargs):
        assert await self._acquire_lock()
        raise RuntimeError("boom")

    async with await _client(db_session, UPLOADER) as client:
        await client.post(f"{BASE}/cashfree/sync")
        monkeypatch.setattr(CashfreeGatewaySyncService, "sync", crash)
        assert await _work(db_session, published) == "failed"
        status = await _status(client)
    assert status["running"] is False and status["queued"] is False
    assert status["last_sync_status"] == "failed" and "RuntimeError" in status["last_sync_error"]


# --- Celery wiring ---------------------------------------------------------------


class _Retried(Exception):
    pass


def _run_task(monkeypatch, outcome: str, retries: int = 0) -> list[dict]:
    asked: list[dict] = []

    async def fake_run(*_args):
        return outcome

    async def passthrough(coro):
        return await coro

    def retry(**kwargs):
        asked.append(kwargs)
        return _Retried()

    monkeypatch.setattr(task_module, "_run_manual", fake_run)
    monkeypatch.setattr(task_module, "run_with_cleanup", passthrough)
    monkeypatch.setattr(sync_cashfree_manual_task, "retry", retry)
    sync_cashfree_manual_task.push_request(retries=retries)
    try:
        sync_cashfree_manual_task.run("00000000-0000-0000-0000-000000000001")
    except _Retried:
        pass
    finally:
        sync_cashfree_manual_task.pop_request()
    return asked


def test_task_retries_only_while_another_sync_holds_the_lock(monkeypatch):
    assert _run_task(monkeypatch, "already_running") == [
        {"countdown": task_module.BUSY_RETRY_SECONDS}
    ]
    for outcome in ("ok", "partial", "failed", "auth_error", "not_configured"):
        assert _run_task(monkeypatch, outcome) == [], outcome
    assert _run_task(monkeypatch, "already_running", task_module.BUSY_MAX_RETRIES) == []


def test_task_is_registered_on_the_queue_the_worker_consumes():
    assert "cashfree.sync_manual" in celery_app.tasks
    assert route_task("cashfree.sync_manual", (), {}, {}) is None
    assert celery_app.conf.task_default_queue == DEFAULT_QUEUE
    assert sync_cashfree_manual_task.time_limit < 15 * 60  # ends before the lock expires
    assert "app.tasks.cashfree_gateway_sync" in celery_app.conf.include

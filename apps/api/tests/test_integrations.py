from __future__ import annotations

import uuid

import pytest
from app.integrations.credentials import EnvCredentialProvider
from app.models.enums import IntegrationStatus, IntegrationType, SyncJobStatus, SyncType
from app.models.integration import Integration, IntegrationCode, SyncJob, WebhookEvent
from app.repositories.integration import IntegrationRepository
from app.repositories.sync_job import SyncJobRepository
from app.services.integration_service import IntegrationService
from app.services.sync_service import SyncService
from app.services.webhook_service import WebhookService, compute_fallback_event_id
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio


async def _make_integration(
    session: AsyncSession, *, code: str = IntegrationCode.SHOPIFY
) -> Integration:
    integration = await IntegrationRepository(session).create(
        name="Shopify",
        code=code,
        type=IntegrationType.ECOMMERCE,
        status=IntegrationStatus.DISCONNECTED,
        enabled=False,
    )
    await session.commit()
    return integration


# 1. Integration creation
async def test_integration_creation(db_session: AsyncSession) -> None:
    integration = await _make_integration(db_session)
    assert integration.id is not None
    assert integration.code == IntegrationCode.SHOPIFY
    assert integration.status == IntegrationStatus.DISCONNECTED
    assert integration.enabled is False


# 2. Integration status via the monitoring API
async def test_integration_status_api(db_session: AsyncSession, make_authenticated_client) -> None:
    integration = await _make_integration(db_session)
    async with await make_authenticated_client(
        db_session, permission_codes=["integrations.read"]
    ) as client:
        response = await client.get(f"/api/v1/integrations/{integration.id}")
        assert response.status_code == 200
        body = response.json()["data"]
        assert body["status"] == "disconnected"
        assert body["code"] == IntegrationCode.SHOPIFY


# 3. SyncJob creation
async def test_sync_job_creation(db_session: AsyncSession) -> None:
    integration = await _make_integration(db_session)
    job = await SyncService(db_session).start_sync(
        integration_id=integration.id, sync_type="incremental", entity_type="orders"
    )
    assert job.status == "queued"
    assert job.entity_type == "orders"
    assert job.records_received == 0


# 4. SyncJob lifecycle
async def test_sync_job_lifecycle(db_session: AsyncSession) -> None:
    integration = await _make_integration(db_session)
    service = SyncService(db_session)

    job = await service.start_sync(
        integration_id=integration.id, sync_type="full", entity_type="customers"
    )
    assert job.status == "queued"

    job = await service.mark_running(job.id)
    assert job.status == "running"
    assert job.started_at is not None

    job = await service.record_progress(job.id, received=10, created=8, updated=2)
    assert job.records_received == 10
    assert job.records_created == 8

    job = await service.complete_sync(job.id, success=True)
    assert job.status == "completed"
    assert job.completed_at is not None

    refreshed = await IntegrationRepository(db_session).get_by_id(integration.id)
    assert refreshed is not None
    assert refreshed.status == "connected"
    assert refreshed.last_successful_sync_at is not None


# 5. SyncError creation (+ PARTIAL status when a job has errors)
async def test_sync_error_creation_marks_job_partial(db_session: AsyncSession) -> None:
    integration = await _make_integration(db_session)
    service = SyncService(db_session)

    job = await service.start_sync(
        integration_id=integration.id, sync_type="incremental", entity_type="orders"
    )
    await service.mark_running(job.id)
    error = await service.record_error(
        job.id,
        entity_type="orders",
        error_type="validation_error",
        error_message="Missing required field 'sku'.",
        external_id="shopify_order_123",
    )
    assert error.resolved is False
    assert error.sync_job_id == job.id

    job = await service.complete_sync(job.id, success=True)
    assert job.error_count == 1
    assert job.status == "partial"


# 6. WebhookEvent creation
async def test_webhook_event_creation(db_session: AsyncSession) -> None:
    integration = await _make_integration(db_session, code=IntegrationCode.SHOPIFY)
    event, created = await WebhookService(db_session).ingest(
        integration_id=integration.id,
        event_type="orders/create",
        payload={"id": "shopify_order_1"},
        external_event_id="evt_1",
    )
    assert created is True
    assert event.status == "received"
    assert event.external_event_id == "evt_1"


# 7 & 8. Webhook idempotency / duplicate delivery
async def test_webhook_idempotency_duplicate_delivery(db_session: AsyncSession) -> None:
    integration = await _make_integration(db_session, code=IntegrationCode.SHOPIFY)
    service = WebhookService(db_session)

    first, created_first = await service.ingest(
        integration_id=integration.id,
        event_type="orders/update",
        payload={"id": "shopify_order_2"},
        external_event_id="evt_dup",
    )
    second, created_second = await service.ingest(
        integration_id=integration.id,
        event_type="orders/update",
        payload={"id": "shopify_order_2", "note": "different payload, same event id"},
        external_event_id="evt_dup",
    )

    assert created_first is True
    assert created_second is False
    assert first.id == second.id

    total = await db_session.execute(select(func.count()).select_from(WebhookEvent))
    assert total.scalar_one() == 1


async def test_webhook_fallback_event_id_is_deterministic() -> None:
    payload = {"awb": "AWB123", "status": "in_transit"}
    first = compute_fallback_event_id(
        integration_code="shiprocket", event_type="tracking", payload=payload
    )
    second = compute_fallback_event_id(
        integration_code="shiprocket", event_type="tracking", payload=payload
    )
    different = compute_fallback_event_id(
        integration_code="shiprocket",
        event_type="tracking",
        payload={**payload, "status": "delivered"},
    )
    assert first == second
    assert first != different


async def test_webhook_idempotency_without_provider_event_id(db_session: AsyncSession) -> None:
    """Providers without a stable event id fall back to a deterministic
    hash — a retried delivery of the identical payload still collides on
    the (integration_id, external_event_id) uniqueness rule.
    """
    integration = await _make_integration(db_session, code=IntegrationCode.SHIPROCKET)
    service = WebhookService(db_session)
    payload = {"awb": "AWB999", "status": "out_for_delivery"}

    first, created_first = await service.ingest(
        integration_id=integration.id, event_type="tracking", payload=payload
    )
    second, created_second = await service.ingest(
        integration_id=integration.id, event_type="tracking", payload=payload
    )

    assert created_first is True
    assert created_second is False
    assert first.id == second.id
    assert first.external_event_id.startswith("fallback:")


# 11. Integration permission checks
async def test_integration_endpoints_require_auth(db_session: AsyncSession) -> None:
    integration = await _make_integration(db_session)
    from app.main import app
    from httpx import ASGITransport, AsyncClient

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(f"/api/v1/integrations/{integration.id}")
        assert response.status_code == 401


async def test_integration_endpoints_require_permission(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    integration = await _make_integration(db_session)
    async with await make_authenticated_client(db_session, permission_codes=[]) as client:
        response = await client.get(f"/api/v1/integrations/{integration.id}")
        assert response.status_code == 403


# 12. Credential protection
async def test_integration_response_never_includes_credentials(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    integration = await _make_integration(db_session)
    async with await make_authenticated_client(
        db_session, permission_codes=["integrations.read"]
    ) as client:
        response = await client.get(f"/api/v1/integrations/{integration.id}")
        body = response.json()["data"]
        assert "access_token" not in body
        assert "api_secret" not in body
        assert "credential" not in body
        assert "password" not in body


async def test_env_credential_provider_reads_namespaced_env_var(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SHOPIFY_ACCESS_TOKEN", "shpat_example")
    provider = EnvCredentialProvider()
    assert provider.get_credential("shopify", "access_token") == "shpat_example"
    assert provider.get_credential("shopify", "nonexistent_key") is None


# 13. Integration health service
async def test_health_check_with_no_registered_adapter_reports_disconnected(
    db_session: AsyncSession,
) -> None:
    # Shopify has an adapter registered as of Phase 2.2 (it reports its own
    # "not configured" outcome — see the Shopify-specific test below), so
    # this exercises the still-current Phase 2.1 fallback via a code that
    # genuinely has no adapter registered.
    integration = await _make_integration(db_session, code=IntegrationCode.META)
    result = await IntegrationService(db_session).run_health_check(integration.id)
    assert result.connected is False
    assert "No adapter registered" in (result.error_message or "")

    refreshed = await IntegrationRepository(db_session).get_by_id(integration.id)
    assert refreshed is not None
    assert refreshed.last_failure_at is not None


async def test_shopify_health_check_reports_not_configured_without_credentials(
    db_session: AsyncSession,
) -> None:
    integration = await _make_integration(db_session, code=IntegrationCode.SHOPIFY)
    result = await IntegrationService(db_session).run_health_check(integration.id)
    assert result.connected is False
    assert "not configured" in (result.error_message or "").lower()

    refreshed = await IntegrationRepository(db_session).get_by_id(integration.id)
    assert refreshed is not None
    assert refreshed.status == "disconnected"


# 14. API endpoints — list/detail/health/sync-history/sync-jobs/webhook-events
async def test_monitoring_endpoints(db_session: AsyncSession, make_authenticated_client) -> None:
    integration = await _make_integration(db_session)
    service = SyncService(db_session)
    job = await service.start_sync(
        integration_id=integration.id, sync_type="full", entity_type="orders"
    )
    await service.mark_running(job.id)
    await service.complete_sync(job.id, success=True)

    await WebhookService(db_session).ingest(
        integration_id=integration.id,
        event_type="orders/create",
        payload={"id": "1"},
        external_event_id="evt_monitoring",
    )

    async with await make_authenticated_client(
        db_session,
        permission_codes=["integrations.read", "sync_jobs.read", "webhooks.read"],
    ) as client:
        listed = await client.get("/api/v1/integrations")
        assert listed.status_code == 200
        assert listed.json()["meta"]["total_items"] >= 1

        history = await client.get(f"/api/v1/integrations/{integration.id}/sync-history")
        assert history.status_code == 200
        assert history.json()["meta"]["total_items"] == 1

        jobs = await client.get("/api/v1/sync-jobs")
        assert jobs.status_code == 200
        assert jobs.json()["meta"]["total_items"] == 1

        job_detail = await client.get(f"/api/v1/sync-jobs/{job.id}")
        assert job_detail.status_code == 200
        assert job_detail.json()["data"]["status"] == "completed"

        events = await client.get("/api/v1/webhook-events")
        assert events.status_code == 200
        assert events.json()["meta"]["total_items"] == 1
        assert "payload" not in events.json()["data"][0]


async def test_webhook_events_filter_by_external_resource_id(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    """New, additive filter (backs the Cashfree payment detail page's
    "Webhook events" section: it needs exactly the deliveries for one
    `cashfree_order_id`, not the whole integration's event log) —
    `integration_id`-only filtering, and no filter at all, must keep
    returning exactly what they did before this was added.
    """
    integration = await _make_integration(db_session)
    webhook_service = WebhookService(db_session)
    await webhook_service.ingest(
        integration_id=integration.id,
        event_type="payment",
        payload={"data": {"order": {"order_id": "AWL1"}}},
        external_event_id="evt_1",
        external_resource_id="AWL1",
    )
    await webhook_service.ingest(
        integration_id=integration.id,
        event_type="payment",
        payload={"data": {"order": {"order_id": "AWL2"}}},
        external_event_id="evt_2",
        external_resource_id="AWL2",
    )

    async with await make_authenticated_client(
        db_session, permission_codes=["webhooks.read"]
    ) as client:
        filtered = await client.get(
            "/api/v1/webhook-events", params={"external_resource_id": "AWL1"}
        )
        assert filtered.status_code == 200
        assert filtered.json()["meta"]["total_items"] == 1
        assert filtered.json()["data"][0]["external_event_id"] == "evt_1"

        unfiltered = await client.get("/api/v1/webhook-events")
        assert unfiltered.status_code == 200
        assert unfiltered.json()["meta"]["total_items"] == 2


async def test_trigger_sync_creates_queued_job_and_requires_manage_permission(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    integration = await _make_integration(db_session)

    async with await make_authenticated_client(
        db_session, permission_codes=["integrations.read"], email="readonly@example.com"
    ) as read_only_client:
        forbidden = await read_only_client.post(
            f"/api/v1/sync/{integration.id}/trigger", json={"entity_type": "orders"}
        )
        assert forbidden.status_code == 403

    async with await make_authenticated_client(
        db_session, permission_codes=["sync_jobs.manage"], email="manager@example.com"
    ) as manager_client:
        triggered = await manager_client.post(
            f"/api/v1/sync/{integration.id}/trigger",
            json={"entity_type": "orders", "sync_type": "incremental"},
        )
        assert triggered.status_code == 202
        assert triggered.json()["data"]["status"] == "queued"


async def test_health_check_endpoint(db_session: AsyncSession, make_authenticated_client) -> None:
    integration = await _make_integration(db_session)
    async with await make_authenticated_client(
        db_session, permission_codes=["integrations.test"]
    ) as client:
        response = await client.post(f"/api/v1/integrations/{integration.id}/health-check")
        assert response.status_code == 200
        assert response.json()["data"]["connected"] is False


async def test_get_integration_not_found_returns_404(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    async with await make_authenticated_client(
        db_session, permission_codes=["integrations.read"]
    ) as client:
        response = await client.get(f"/api/v1/integrations/{uuid.uuid4()}")
        assert response.status_code == 404


# --- Real production incident: 8 separate `shipments` SyncJobs ended up
# stuck simultaneously RUNNING, several orphaned for 18+ hours after a
# worker restart killed them mid-flight -- nothing prevented the
# scheduler/manual triggers from starting a second concurrent sync for
# the same (integration, entity_type), and nothing ever marked an
# orphaned job as failed once its process died. ------------------------


async def test_start_sync_refuses_a_second_concurrent_job_for_the_same_entity_type(
    db_session: AsyncSession,
) -> None:
    integration = await _make_integration(db_session)
    service = SyncService(db_session)

    job1 = await service.start_sync(
        integration_id=integration.id, sync_type="incremental", entity_type="orders"
    )
    assert job1.status == "queued"

    from app.core.exceptions import ConflictError

    with pytest.raises(ConflictError):
        await service.start_sync(
            integration_id=integration.id, sync_type="incremental", entity_type="orders"
        )


async def test_start_sync_allows_a_new_job_once_the_previous_one_is_complete(
    db_session: AsyncSession,
) -> None:
    integration = await _make_integration(db_session)
    service = SyncService(db_session)

    job1 = await service.start_sync(
        integration_id=integration.id, sync_type="incremental", entity_type="orders"
    )
    await service.complete_sync(job1.id, success=True)

    job2 = await service.start_sync(
        integration_id=integration.id, sync_type="incremental", entity_type="orders"
    )
    assert job2.id != job1.id
    assert job2.status == "queued"


async def test_start_sync_allows_concurrent_jobs_for_different_entity_types(
    db_session: AsyncSession,
) -> None:
    """The guard is scoped to (integration, entity_type) -- a real
    multi-entity integration (Shopify: orders/customers/products) must
    still be able to sync several entity types at once.
    """
    integration = await _make_integration(db_session)
    service = SyncService(db_session)

    orders_job = await service.start_sync(
        integration_id=integration.id, sync_type="incremental", entity_type="orders"
    )
    customers_job = await service.start_sync(
        integration_id=integration.id, sync_type="incremental", entity_type="customers"
    )
    assert orders_job.id != customers_job.id


async def test_full_customer_sync_allowed_when_no_active_customer_sync(
    db_session: AsyncSession,
) -> None:
    integration = await _make_integration(db_session)
    service = SyncService(db_session)

    job = await service.start_sync(
        integration_id=integration.id, sync_type="full", entity_type="customers"
    )
    assert job.status == "queued"


async def test_full_customer_sync_allowed_after_a_failed_customer_sync(
    db_session: AsyncSession,
) -> None:
    integration = await _make_integration(db_session)
    service = SyncService(db_session)

    job1 = await service.start_sync(
        integration_id=integration.id, sync_type="full", entity_type="customers"
    )
    await service.complete_sync(job1.id, success=False)

    job2 = await service.start_sync(
        integration_id=integration.id, sync_type="full", entity_type="customers"
    )
    assert job2.id != job1.id
    assert job2.status == "queued"


async def test_full_customer_sync_allowed_after_a_completed_customer_sync(
    db_session: AsyncSession,
) -> None:
    integration = await _make_integration(db_session)
    service = SyncService(db_session)

    job1 = await service.start_sync(
        integration_id=integration.id, sync_type="full", entity_type="customers"
    )
    await service.complete_sync(job1.id, success=True)

    job2 = await service.start_sync(
        integration_id=integration.id, sync_type="full", entity_type="customers"
    )
    assert job2.id != job1.id
    assert job2.status == "queued"


async def test_sync_history_keeps_old_completed_and_failed_jobs_visible(
    db_session: AsyncSession,
) -> None:
    """Completed/failed/partial/cancelled rows are never deleted -- only
    `status` distinguishes an old, finished job from a currently-active
    one; sync history must keep showing every one of them.
    """
    integration = await _make_integration(db_session)
    service = SyncService(db_session)

    completed = await service.start_sync(
        integration_id=integration.id, sync_type="full", entity_type="customers"
    )
    await service.complete_sync(completed.id, success=True)
    failed = await service.start_sync(
        integration_id=integration.id, sync_type="full", entity_type="customers"
    )
    await service.complete_sync(failed.id, success=False)
    cancelled = await service.start_sync(
        integration_id=integration.id, sync_type="full", entity_type="customers"
    )
    await service.cancel_sync(cancelled.id)

    history = (
        (await db_session.execute(select(SyncJob).where(SyncJob.integration_id == integration.id)))
        .scalars()
        .all()
    )
    assert {job.id for job in history} == {completed.id, failed.id, cancelled.id}
    assert {job.status.value for job in history} == {"completed", "failed", "cancelled"}


async def test_partial_unique_index_rejects_a_second_active_job_created_outside_start_sync(
    db_session: AsyncSession,
) -> None:
    """DB-level backstop for `start_sync`'s check-then-create race window
    (two near-simultaneous trigger requests can both pass the
    `get_active_for_entity` check before either commits). Bypasses
    `start_sync` entirely and inserts two active rows directly through
    the repository, proving `uq_sync_jobs_one_active_per_entity` itself
    -- not just the application-level check -- refuses a second QUEUED/
    RUNNING row for the same (integration, entity_type).
    """
    from sqlalchemy.exc import IntegrityError

    integration = await _make_integration(db_session)
    repo = SyncJobRepository(db_session)

    await repo.create(
        integration_id=integration.id,
        sync_type=SyncType.FULL,
        entity_type="customers",
        status=SyncJobStatus.QUEUED,
    )
    await db_session.commit()

    with pytest.raises(IntegrityError):
        await repo.create(
            integration_id=integration.id,
            sync_type=SyncType.FULL,
            entity_type="customers",
            status=SyncJobStatus.RUNNING,
        )
        await db_session.commit()
    await db_session.rollback()


async def test_start_sync_converts_the_race_backstop_into_a_conflict_error(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If two requests somehow both pass `get_active_for_entity` (the
    genuine race `uq_sync_jobs_one_active_per_entity` exists for),
    `start_sync` must still surface the same `ConflictError` the normal
    path raises -- never a raw 500 from an uncaught `IntegrityError`.
    Simulated by monkeypatching the guard check to force the race window
    rather than relying on real thread-level concurrency timing.
    """
    from app.core.exceptions import ConflictError

    integration = await _make_integration(db_session)
    service = SyncService(db_session)

    existing = await service.start_sync(
        integration_id=integration.id, sync_type="full", entity_type="customers"
    )

    # Force only the *first* check (the one `start_sync` makes before
    # attempting the insert) to miss the already-active job, simulating
    # the real race -- another request's job committed in the gap between
    # that check and this one's insert. The post-rollback re-check must
    # go through to the real method and find it, exactly as it would in
    # production once the concurrent insert has actually landed.
    real_get_active_for_entity = service.sync_jobs.get_active_for_entity
    calls = {"count": 0}

    async def _miss_once_then_real(**kwargs: object) -> object:
        calls["count"] += 1
        if calls["count"] == 1:
            return None
        return await real_get_active_for_entity(**kwargs)

    monkeypatch.setattr(service.sync_jobs, "get_active_for_entity", _miss_once_then_real)

    with pytest.raises(ConflictError) as exc_info:
        await service.start_sync(
            integration_id=integration.id, sync_type="full", entity_type="customers"
        )
    assert exc_info.value.details["sync_job_id"] == str(existing.id)


async def test_run_sync_returns_the_existing_active_job_instead_of_erroring(
    db_session: AsyncSession,
) -> None:
    """The scheduler calls `run_sync` every 10 minutes -- it must never
    raise just because a previous cycle's job for this entity type is
    still active; it should treat that as "nothing to do."
    """
    integration = await _make_integration(db_session)
    service = SyncService(db_session)

    job1 = await service.start_sync(
        integration_id=integration.id, sync_type="incremental", entity_type="orders"
    )

    result = await service.run_sync(
        integration_id=integration.id, sync_type="incremental", entity_type="orders"
    )
    assert result.id == job1.id
    assert result.status == "queued"  # untouched -- no second execution attempted


async def test_reap_stale_sync_jobs_fails_a_job_with_no_recent_progress(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import UTC, datetime, timedelta

    from app.tasks import sync_tasks

    integration = await _make_integration(db_session)
    service = SyncService(db_session)
    job = await service.start_sync(
        integration_id=integration.id, sync_type="full", entity_type="shipments"
    )
    await service.mark_running(job.id)

    # Simulate a job whose worker process died a long time ago: its
    # updated_at (the real heartbeat -- bumped by every record_progress/
    # record_error call) hasn't moved since well past the threshold.
    from app.repositories.sync_job import SyncJobRepository

    stale_time = datetime.now(UTC) - timedelta(minutes=30)
    await SyncJobRepository(db_session).update(job, updated_at=stale_time)
    await db_session.commit()

    monkeypatch.setattr(
        sync_tasks, "AsyncSessionLocal", lambda: db_session_cm_for_reaper(db_session)
    )

    reaped = await sync_tasks._reap_stale_sync_jobs()

    assert str(job.id) in reaped
    refreshed = await service.sync_jobs.get_by_id(job.id)
    assert refreshed.status == "failed"


async def test_stale_running_customer_sync_does_not_permanently_block_a_new_full_sync(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reported bug, reproduced directly: a `customers` job stuck
    RUNNING (worker died mid-sync, or an unexpected exception before this
    session's `execute_sync` fix) must not block Full Sync forever -- the
    stale-job reaper clears it and a fresh sync is immediately allowed.
    """
    from datetime import UTC, datetime, timedelta

    from app.core.exceptions import ConflictError
    from app.tasks import sync_tasks

    integration = await _make_integration(db_session)
    service = SyncService(db_session)
    stuck_job = await service.start_sync(
        integration_id=integration.id, sync_type="full", entity_type="customers"
    )
    await service.mark_running(stuck_job.id)

    with pytest.raises(ConflictError):
        await service.start_sync(
            integration_id=integration.id, sync_type="full", entity_type="customers"
        )

    stale_time = datetime.now(UTC) - timedelta(minutes=30)
    await SyncJobRepository(db_session).update(stuck_job, updated_at=stale_time)
    await db_session.commit()

    monkeypatch.setattr(
        sync_tasks, "AsyncSessionLocal", lambda: db_session_cm_for_reaper(db_session)
    )
    reaped = await sync_tasks._reap_stale_sync_jobs()
    assert str(stuck_job.id) in reaped

    refreshed = await service.sync_jobs.get_by_id(stuck_job.id)
    assert refreshed.status == "failed"

    new_job = await service.start_sync(
        integration_id=integration.id, sync_type="full", entity_type="customers"
    )
    assert new_job.id != stuck_job.id
    assert new_job.status == "queued"


async def test_reap_stale_sync_jobs_leaves_a_recently_updated_job_alone(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A genuinely slow-but-alive job (e.g. a large historical crawl,
    confirmed live this engagement to take well over an hour) must never
    be reaped just because it's been running a long time -- only the
    absence of *recent* progress matters.
    """
    from app.tasks import sync_tasks

    integration = await _make_integration(db_session)
    service = SyncService(db_session)
    job = await service.start_sync(
        integration_id=integration.id, sync_type="full", entity_type="shipments"
    )
    await service.mark_running(job.id)
    await service.record_progress(job.id, received=50, updated=50)  # bumps updated_at to "now"

    monkeypatch.setattr(
        sync_tasks, "AsyncSessionLocal", lambda: db_session_cm_for_reaper(db_session)
    )

    reaped = await sync_tasks._reap_stale_sync_jobs()

    assert str(job.id) not in reaped
    refreshed = await service.sync_jobs.get_by_id(job.id)
    assert refreshed.status == "running"


async def test_reap_stale_sync_jobs_fails_a_queued_job_that_no_worker_ever_started(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Production incident: `start_sync`'s one-active-job guard counts
    QUEUED as active, but the reaper only ever recovered RUNNING jobs --
    so a single orphaned QUEUED `orders` job (lost broker message, worker
    killed in the QUEUED->RUNNING window, or the manual-trigger enqueue
    failing on a broker outage) wedged every subsequent scheduled orders
    sync forever. The reaper must now clear a long-QUEUED job too.
    """
    from datetime import UTC, datetime, timedelta

    from app.repositories.sync_job import SyncJobRepository
    from app.tasks import sync_tasks

    integration = await _make_integration(db_session)
    service = SyncService(db_session)
    job = await service.start_sync(
        integration_id=integration.id, sync_type="incremental", entity_type="orders"
    )
    assert job.status == "queued"

    stale_time = datetime.now(UTC) - timedelta(minutes=30)
    await SyncJobRepository(db_session).update(
        job, created_at=stale_time, updated_at=stale_time
    )
    await db_session.commit()

    monkeypatch.setattr(
        sync_tasks, "AsyncSessionLocal", lambda: db_session_cm_for_reaper(db_session)
    )

    reaped = await sync_tasks._reap_stale_sync_jobs()

    assert str(job.id) in reaped
    refreshed = await service.sync_jobs.get_by_id(job.id)
    assert refreshed.status == "failed"

    # The wedge is gone: a fresh scheduled run can now start a new job.
    new_job = await service.start_sync(
        integration_id=integration.id, sync_type="incremental", entity_type="orders"
    )
    assert new_job.id != job.id


async def test_reap_stale_sync_jobs_leaves_a_freshly_queued_job_alone(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A healthy QUEUED job becomes RUNNING within seconds -- one that was
    only just created must never be reaped out from under the worker
    about to pick it up.
    """
    from app.tasks import sync_tasks

    integration = await _make_integration(db_session)
    service = SyncService(db_session)
    job = await service.start_sync(
        integration_id=integration.id, sync_type="incremental", entity_type="orders"
    )

    monkeypatch.setattr(
        sync_tasks, "AsyncSessionLocal", lambda: db_session_cm_for_reaper(db_session)
    )

    reaped = await sync_tasks._reap_stale_sync_jobs()

    assert str(job.id) not in reaped
    refreshed = await service.sync_jobs.get_by_id(job.id)
    assert refreshed.status == "queued"


class db_session_cm_for_reaper:
    """Same wrapper as `test_scheduled_sync.py`'s `db_session_cm` --
    duplicated locally rather than imported to avoid coupling two
    unrelated test modules together over a small test-only helper.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def __aenter__(self) -> AsyncSession:
        return self._session

    async def __aexit__(self, *exc_info: object) -> None:
        pass

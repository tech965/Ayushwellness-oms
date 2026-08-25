from __future__ import annotations

import uuid

import pytest
from app.integrations.credentials import EnvCredentialProvider
from app.models.enums import IntegrationStatus, IntegrationType
from app.models.integration import Integration, IntegrationCode, WebhookEvent
from app.repositories.integration import IntegrationRepository
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

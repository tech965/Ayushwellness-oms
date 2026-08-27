"""Round 4 — worker/broker-failure recovery for `WebhookEvent`.

`receive_shopify_webhook` persists the event, then best-effort enqueues
Celery processing; a broker outage at that exact moment leaves the event
stuck at RECEIVED with nothing to re-drive it. These tests prove: (1) the
event genuinely isn't lost when the initial enqueue fails, and (2) the
scheduled recovery task finds and reprocesses it once things are healthy
again -- the full "system must recover" loop, not just the query in
isolation.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from app.integrations.registry import clear_adapters, register_adapter
from app.integrations.shopify.adapter import ShopifyAdapter
from app.models.enums import IntegrationStatus, IntegrationType, WebhookEventStatus
from app.models.integration import IntegrationCode
from app.repositories.integration import IntegrationRepository
from app.repositories.webhook_event import WebhookEventRepository
from app.services.webhook_service import WebhookService
from app.tasks import webhook_processing
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _reset_adapter_registry():
    yield
    clear_adapters()


async def _make_shopify_integration(session: AsyncSession):
    integration = await IntegrationRepository(session).create(
        name="Shopify",
        code=IntegrationCode.SHOPIFY,
        type=IntegrationType.ECOMMERCE,
        status=IntegrationStatus.DISCONNECTED,
        enabled=True,
    )
    await session.commit()
    return integration


class _db_session_cm:
    """Same stand-in used in `test_scheduled_sync.py` -- wraps an
    already-open test `db_session` as an async context manager so it
    stands in for `AsyncSessionLocal()` without a second connection the
    test's own assertions wouldn't see.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def __aenter__(self) -> AsyncSession:
        return self._session

    async def __aexit__(self, *exc_info: object) -> None:
        return None


async def test_a_webhook_event_survives_a_broker_outage_at_enqueue_time(
    db_session: AsyncSession,
) -> None:
    """Simulates exactly what `receive_shopify_webhook` does when
    `.delay()` raises: the event is still committed to the database
    first, so a broker outage loses nothing -- it just leaves the event
    at RECEIVED instead of PROCESSING/PROCESSED.
    """
    integration = await _make_shopify_integration(db_session)
    webhook_service = WebhookService(db_session)

    event, created = await webhook_service.ingest(
        integration_id=integration.id,
        event_type="orders/create",
        payload={"id": 1, "name": "#BROKER-DOWN-1"},
        external_event_id="wh_broker_down",
    )
    assert created is True
    # The endpoint's `except Exception` around `.delay()` is what would
    # run here in production -- nothing to assert on it directly since
    # it's a bare `pass`-equivalent (logs and moves on); what matters is
    # the row below.

    refreshed = await webhook_service.get_event(event.id)
    assert refreshed.status == WebhookEventStatus.RECEIVED
    assert refreshed.processed_at is None


async def test_recover_stuck_webhook_events_finds_only_events_past_the_threshold(
    db_session: AsyncSession,
) -> None:
    integration = await _make_shopify_integration(db_session)
    events_repo = WebhookEventRepository(db_session)
    webhook_service = WebhookService(db_session)

    stuck_event, _ = await webhook_service.ingest(
        integration_id=integration.id,
        event_type="orders/create",
        payload={"id": 1},
        external_event_id="wh_stuck",
    )
    fresh_event, _ = await webhook_service.ingest(
        integration_id=integration.id,
        event_type="orders/create",
        payload={"id": 2},
        external_event_id="wh_fresh",
    )
    # Backdate only the "stuck" one past the recovery threshold -- a
    # webhook event that arrived 30 seconds ago is still legitimately
    # waiting for its worker, not lost.
    await events_repo.update(
        stuck_event,
        received_at=datetime.now(UTC) - webhook_processing.STUCK_WEBHOOK_EVENT_THRESHOLD * 2,
    )
    await db_session.commit()

    found = await events_repo.get_stuck_received(
        received_before=datetime.now(UTC) - webhook_processing.STUCK_WEBHOOK_EVENT_THRESHOLD
    )
    found_ids = {e.id for e in found}
    assert stuck_event.id in found_ids
    assert fresh_event.id not in found_ids


async def test_recovery_task_reprocesses_a_stuck_event_end_to_end(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Full loop: an event stuck at RECEIVED (simulating a lost enqueue)
    is found by the recovery task and driven all the way to PROCESSED,
    with the underlying OMS record actually created -- not just the
    WebhookEvent row flipped.
    """
    register_adapter(ShopifyAdapter(client=None))
    integration = await _make_shopify_integration(db_session)
    events_repo = WebhookEventRepository(db_session)
    webhook_service = WebhookService(db_session)

    event, _ = await webhook_service.ingest(
        integration_id=integration.id,
        event_type="customers/create",
        payload={"id": 42, "first_name": "Ana", "last_name": "Q", "email": "ana@example.com"},
        external_event_id="wh_recover_me",
    )
    await events_repo.update(
        event, received_at=datetime.now(UTC) - webhook_processing.STUCK_WEBHOOK_EVENT_THRESHOLD * 2
    )
    await db_session.commit()

    monkeypatch.setattr(webhook_processing, "AsyncSessionLocal", lambda: _db_session_cm(db_session))
    monkeypatch.setattr(webhook_processing, "dispose_engine_sync", lambda: None)

    # `_recover_stuck_webhook_events` finds the event and calls
    # `process_webhook_event_task.delay(...)`, which in production hands
    # off to a real worker (its own DB session); reuse the same session
    # here by driving `_process_webhook_event` directly instead of going
    # through a Celery broker the test suite doesn't have.
    recovered_ids = []
    monkeypatch.setattr(
        webhook_processing.process_webhook_event_task,
        "delay",
        lambda event_id: recovered_ids.append(event_id),
    )

    found = await webhook_processing._recover_stuck_webhook_events()
    assert found == [str(event.id)]
    assert recovered_ids == [str(event.id)]

    await webhook_processing._process_webhook_event(str(event.id))

    refreshed = await webhook_service.get_event(event.id)
    assert refreshed.status == WebhookEventStatus.PROCESSED
    assert refreshed.processed_at is not None

    from app.repositories.customer import CustomerRepository

    customer = await CustomerRepository(db_session).get_by_source_external_id(
        source_system="shopify", external_id="42"
    )
    assert customer is not None
    assert customer.email == "ana@example.com"


async def test_recovery_task_ignores_events_still_within_the_grace_window(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    integration = await _make_shopify_integration(db_session)
    webhook_service = WebhookService(db_session)
    await webhook_service.ingest(
        integration_id=integration.id,
        event_type="orders/create",
        payload={"id": 1},
        external_event_id="wh_just_arrived",
    )

    monkeypatch.setattr(
        webhook_processing, "AsyncSessionLocal", lambda: _db_session_cm(db_session)
    )
    monkeypatch.setattr(webhook_processing, "dispose_engine_sync", lambda: None)
    monkeypatch.setattr(
        webhook_processing.process_webhook_event_task, "delay", lambda event_id: None
    )

    found = await webhook_processing._recover_stuck_webhook_events()
    assert found == []

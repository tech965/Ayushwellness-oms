"""Regression tests for the scheduled-sync backstop
(`app.tasks.sync_tasks.run_scheduled_sync_task`) — added after a live
Shopify-vs-OMS reconciliation found that, with zero webhooks registered
and no periodic schedule, new Shopify orders never reached the OMS
except via a manual "Trigger Sync" click. This only tests the
enqueueing logic (`_run_scheduled_sync`), not Celery beat itself or a
real broker.
"""

from __future__ import annotations

import pytest
from app.integrations.registry import clear_adapters, register_adapter
from app.integrations.shiprocket.adapter import ShiprocketAdapter
from app.integrations.shopify.adapter import ShopifyAdapter
from app.models.enums import IntegrationStatus, IntegrationType, SyncType
from app.models.integration import Integration, IntegrationCode
from app.repositories.integration import IntegrationRepository
from app.tasks import sync_tasks
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _reset_adapter_registry():
    yield
    clear_adapters()


async def _make_integration(session: AsyncSession, code: str) -> Integration:
    integration = await IntegrationRepository(session).create(
        name=code,
        code=code,
        type=IntegrationType.ECOMMERCE,
        status=IntegrationStatus.DISCONNECTED,
        enabled=True,
    )
    await session.commit()
    return integration


async def test_scheduled_sync_enqueues_only_entities_a_registered_adapter_supports(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    shopify_integration = await _make_integration(db_session, IntegrationCode.SHOPIFY)
    # No adapter is registered for this one — matches every real
    # not-yet-implemented provider (Blue Dart, Delhivery, ...).
    await _make_integration(db_session, IntegrationCode.BLUE_DART)

    register_adapter(ShopifyAdapter(client=object()))  # never actually called — just needs to exist

    calls: list[tuple[str, str, str]] = []
    monkeypatch.setattr(
        sync_tasks.run_sync_task,
        "delay",
        lambda integration_id, sync_type, entity_type: calls.append(
            (integration_id, sync_type, entity_type)
        ),
    )
    monkeypatch.setattr(sync_tasks, "AsyncSessionLocal", lambda: db_session_cm(db_session))

    enqueued = await sync_tasks._run_scheduled_sync()

    assert enqueued == [
        (IntegrationCode.SHOPIFY, "orders"),
        (IntegrationCode.SHOPIFY, "customers"),
        (IntegrationCode.SHOPIFY, "products"),
        (IntegrationCode.SHOPIFY, "abandoned_checkouts"),
    ]
    # Regression test for the historical-backlog production incident: a
    # brand-new entity with no completed baseline and no `backlog_complete`
    # flag must be scheduled as FULL (backlog), never INCREMENTAL -- the
    # scheduler used to hand out INCREMENTAL unconditionally, which let a
    # real historical gap (Shopify orders never imported) look, from Sync
    # History alone, like an ordinary "nothing new" incremental run.
    assert calls == [
        (str(shopify_integration.id), SyncType.FULL.value, "orders"),
        (str(shopify_integration.id), SyncType.FULL.value, "customers"),
        (str(shopify_integration.id), SyncType.FULL.value, "products"),
        (str(shopify_integration.id), SyncType.FULL.value, "abandoned_checkouts"),
    ]


async def test_scheduled_sync_enqueues_shiprocket_shipments_before_ndr(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`shipments` must be enqueued ahead of `ndr` — an NDR is only
    matchable to an OMS shipment the shipment sync has already imported
    (see `app.integrations.entity_sync._upsert_shipment`). Same-cycle
    ordering, not a hard guarantee (each runs as its own `SyncJob`), but
    the enqueue order should still reflect the intended data-flow
    dependency.
    """
    shiprocket_integration = await _make_integration(db_session, IntegrationCode.SHIPROCKET)
    register_adapter(ShiprocketAdapter(client=object()))

    calls: list[tuple[str, str, str]] = []
    monkeypatch.setattr(
        sync_tasks.run_sync_task,
        "delay",
        lambda integration_id, sync_type, entity_type: calls.append(
            (integration_id, sync_type, entity_type)
        ),
    )
    monkeypatch.setattr(sync_tasks, "AsyncSessionLocal", lambda: db_session_cm(db_session))

    enqueued = await sync_tasks._run_scheduled_sync()

    assert enqueued == [
        (IntegrationCode.SHIPROCKET, "shipments"),
        (IntegrationCode.SHIPROCKET, "ndr"),
    ]
    # No completed baseline for either entity yet -- both scheduled as
    # FULL (backlog), same reasoning as the Shopify test above.
    assert calls == [
        (str(shiprocket_integration.id), SyncType.FULL.value, "shipments"),
        (str(shiprocket_integration.id), SyncType.FULL.value, "ndr"),
    ]


async def test_scheduled_sync_uses_incremental_once_backlog_is_known_complete(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Once an entity's backlog has genuinely finished (the explicit
    `backlog_complete` flag on `Integration.configuration`, set only by
    `SyncService._run_entity_sync` observing a real `has_more=False` from
    a non-incremental fetch), the scheduler goes back to the cheap
    INCREMENTAL path -- this isn't a one-way "always FULL now" change.
    """
    shopify_integration = await _make_integration(db_session, IntegrationCode.SHOPIFY)
    await IntegrationRepository(db_session).update(
        shopify_integration, configuration={"backlog_complete": {"orders": True}}
    )
    await db_session.commit()
    register_adapter(ShopifyAdapter(client=object()))

    calls: list[tuple[str, str, str]] = []
    monkeypatch.setattr(
        sync_tasks.run_sync_task,
        "delay",
        lambda integration_id, sync_type, entity_type: calls.append(
            (integration_id, sync_type, entity_type)
        ),
    )
    monkeypatch.setattr(sync_tasks, "AsyncSessionLocal", lambda: db_session_cm(db_session))

    await sync_tasks._run_scheduled_sync()

    orders_call = next(c for c in calls if c[2] == "orders")
    assert orders_call[1] == SyncType.INCREMENTAL.value
    # customers/products never got their own `backlog_complete` flag --
    # each entity's completion is tracked independently.
    assert next(c for c in calls if c[2] == "customers")[1] == SyncType.FULL.value
    assert next(c for c in calls if c[2] == "products")[1] == SyncType.FULL.value


async def test_scheduled_sync_enqueues_nothing_for_a_genuinely_unimplemented_provider(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Round 9: `get_adapter` now self-heals a registry that's merely
    *momentarily* empty for a provider that really does have a
    `register()` (see `app.integrations.registry.get_adapter`'s
    docstring) — so this test can no longer use Shopify (which does)
    to prove "no adapter -> nothing enqueued"; self-healing would find
    it and enqueue its entities, which is the whole point of that fix.
    Blue Dart has no adapter at all to find, self-heal or not, so it's
    the only honest way left to exercise this branch.
    """
    await _make_integration(db_session, IntegrationCode.BLUE_DART)

    calls: list[tuple[str, str, str]] = []
    monkeypatch.setattr(
        sync_tasks.run_sync_task,
        "delay",
        lambda integration_id, sync_type, entity_type: calls.append(
            (integration_id, sync_type, entity_type)
        ),
    )
    monkeypatch.setattr(sync_tasks, "AsyncSessionLocal", lambda: db_session_cm(db_session))

    enqueued = await sync_tasks._run_scheduled_sync()

    assert enqueued == []
    assert calls == []


class db_session_cm:
    """Wraps an already-open test `db_session` as an async context manager
    so it stands in for `AsyncSessionLocal()` without opening a second,
    separate connection the test's own assertions wouldn't see.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def __aenter__(self) -> AsyncSession:
        return self._session

    async def __aexit__(self, *exc_info: object) -> None:
        return None

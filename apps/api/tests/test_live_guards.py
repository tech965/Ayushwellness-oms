"""Live-mode write guards for every provider: no sync, reconciliation or
tracking task may persist provider data, and Shopify fulfilment retry
works for reference-only shipments. Providers are MOCKED."""

from __future__ import annotations

import pytest
from app.core.config import settings
from app.db.session import get_db
from app.integrations.registry import clear_adapters, register_adapter
from app.integrations.shiprocket.adapter import ShiprocketAdapter
from app.integrations.shopify.adapter import ShopifyAdapter
from app.main import app
from app.models.enums import (
    IntegrationStatus,
    IntegrationType,
    OrderStatus,
    ShopifySyncStatus,
    SyncJobStatus,
    SyncType,
)
from app.models.integration import IntegrationCode, SyncJob
from app.models.order_workflow import OrderWorkflow
from app.models.shipment import Shipment, ShipmentEvent
from app.models.shipment_ref import ShipmentRef
from app.repositories.integration import IntegrationRepository
from app.repositories.sync_job import SyncJobRepository
from app.services.order_ref import encode_shopify_order_ref
from app.services.sync_service import SyncService
from app.tasks import reconciliation_tasks, shiprocket_sync, sync_tasks
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.telecalling_test_utils import bearer_client, make_role, make_user
from tests.test_live_orders_workflow import _node
from tests.test_live_shiprocket import ShiprocketStub, ShopifyStub
from tests.test_live_telecalling_workflow import RouterStub
from tests.test_shopify_live_mode import _count

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _live(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "SHOPIFY_LIVE_MODE", True)
    yield
    clear_adapters()
    app.dependency_overrides.clear()


class _Ctx:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def __aenter__(self) -> AsyncSession:
        return self._session

    async def __aexit__(self, *exc) -> None:
        return None


@pytest.mark.parametrize(
    "code", [IntegrationCode.SHIPROCKET, IntegrationCode.CASHFREE, IntegrationCode.SHOPIFY]
)
async def test_manual_sync_job_is_refused_for_every_provider(
    db_session: AsyncSession, code: str
) -> None:
    integration = await IntegrationRepository(db_session).create(
        name=code,
        code=code,
        type=IntegrationType.ECOMMERCE,
        status=IntegrationStatus.CONNECTED,
        enabled=True,
    )
    job = await SyncJobRepository(db_session).create(
        integration_id=integration.id,
        sync_type=SyncType.FULL.value,
        entity_type="orders" if code == IntegrationCode.SHOPIFY else "shipments",
        status=SyncJobStatus.QUEUED,
    )
    await db_session.commit()
    register_adapter(ShopifyAdapter(client=RouterStub({1: _node(1)})))
    register_adapter(ShiprocketAdapter(client=ShiprocketStub()))

    result = await SyncService(db_session).execute_sync(job.id)

    assert result.status == SyncJobStatus.FAILED
    assert await _count(db_session, Shipment) == 0
    assert await _count(db_session, ShipmentEvent) == 0


async def test_scheduled_sync_skips_every_provider(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    for code in (IntegrationCode.SHOPIFY, IntegrationCode.SHIPROCKET):
        await IntegrationRepository(db_session).create(
            name=code,
            code=code,
            type=IntegrationType.ECOMMERCE,
            status=IntegrationStatus.CONNECTED,
            enabled=True,
        )
    await db_session.commit()
    register_adapter(ShopifyAdapter(client=RouterStub({})))
    register_adapter(ShiprocketAdapter(client=ShiprocketStub()))
    monkeypatch.setattr(sync_tasks, "AsyncSessionLocal", lambda: _Ctx(db_session))
    enqueued: list = []
    monkeypatch.setattr(sync_tasks.run_sync_task, "delay", lambda *a: enqueued.append(a))

    assert await sync_tasks._run_scheduled_sync() == []
    assert enqueued == []


async def test_tracking_refresh_and_reconciliation_tasks_do_nothing(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(shiprocket_sync, "AsyncSessionLocal", lambda: _Ctx(db_session))
    monkeypatch.setattr(
        reconciliation_tasks, "AsyncSessionLocal", lambda: _Ctx(db_session), raising=False
    )

    await shiprocket_sync._execute_tracking_refresh("00000000-0000-0000-0000-000000000000")
    await reconciliation_tasks._execute_reconciliation_run("00000000-0000-0000-0000-000000000000")

    assert await _count(db_session, SyncJob) == 0
    assert await _count(db_session, ShipmentEvent) == 0


async def test_reconciliation_api_refuses_in_live_mode(db_session: AsyncSession) -> None:
    role = await make_role(db_session, name="REC", permission_codes=["reconciliation.read"])
    user = await make_user(db_session, email="rec@example.com", role=role)

    async with bearer_client(app, get_db, db_session, user.id) as client:
        response = await client.get("/api/v1/reconciliation/runs")

    assert response.status_code == 422
    assert "live-data mode" in response.text


async def test_shopify_fulfilment_retry_for_reference_only_shipment(
    db_session: AsyncSession,
) -> None:
    role = await make_role(
        db_session, name="OPS2", permission_codes=["shipments.update", "shipments.read"]
    )
    user = await make_user(db_session, email="ops2@example.com", role=role)
    workflow = OrderWorkflow(
        id=encode_shopify_order_ref("1"), external_order_id="1", status=OrderStatus.CONFIRMED
    )
    db_session.add(workflow)
    await db_session.flush()
    ref = ShipmentRef(
        order_id=workflow.id,
        awb="AWB123",
        shiprocket_shipment_id="777",
        shopify_sync_status=ShopifySyncStatus.FAILED,
        shopify_sync_error="boom",
    )
    db_session.add(ref)
    await db_session.commit()
    shopify = ShopifyStub({1: _node(1)})
    register_adapter(ShopifyAdapter(client=shopify))
    shiprocket = ShiprocketStub()
    register_adapter(ShiprocketAdapter(client=shiprocket))

    async with bearer_client(app, get_db, db_session, user.id) as client:
        response = await client.post(f"/api/v1/shipments/{ref.id}/shopify/retry-sync")

    assert response.status_code == 200, response.text
    await db_session.refresh(ref)
    assert ref.shopify_sync_status == ShopifySyncStatus.SYNCED
    assert (
        shopify.fulfilled
        and shopify.fulfilled[0]["fulfillment"]["trackingInfo"]["number"] == "AWB123"
    )
    assert (await db_session.execute(select(Shipment))).first() is None


async def test_unsupported_operations_are_listed_not_simulated(db_session: AsyncSession) -> None:
    role = await make_role(db_session, name="ANY", permission_codes=["orders.read"])
    user = await make_user(db_session, email="any@example.com", role=role)

    async with bearer_client(app, get_db, db_session, user.id) as client:
        response = await client.get("/api/v1/analytics/live/capabilities")

    operations = {row["operation"]: row["reason"] for row in response.json()["data"]}
    assert "shopify_refund_creation" in operations
    assert "shopify_order_cancellation" not in operations  # implemented per Shopify docs
    assert all(reason for reason in operations.values())

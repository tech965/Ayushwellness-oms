"""End-to-end Shiprocket sync:

- NDR: a genuine provider-paginated list, routed through the same
  generic `SyncService.execute_sync` loop Shopify uses (unchanged).
- Tracking: OMS-shipment-driven, routed through
  `app.integrations.shiprocket.sync.refresh_tracking` (composes
  `SyncService`'s primitives directly — see that module's docstring).
- RTO: derived from tracking events (no separate list endpoint could be
  confirmed for Shiprocket).

No real Shiprocket account; the stub client returns hand-built response
shapes.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from app.integrations.registry import clear_adapters, register_adapter
from app.integrations.shiprocket.adapter import ShiprocketAdapter
from app.integrations.shiprocket.sync import refresh_tracking
from app.models.enums import (
    IntegrationStatus,
    IntegrationType,
    PaymentType,
    ShipmentStatus,
    SyncType,
)
from app.models.integration import Integration, IntegrationCode
from app.models.ndr import NDR
from app.models.rto import RTO
from app.models.shipment import ShipmentEvent
from app.repositories.integration import IntegrationRepository
from app.repositories.shipment import ShipmentRepository
from app.services.order_service import OrderService
from app.services.shipment_service import ShipmentService
from app.services.sync_service import SyncService
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio


class _StubClient:
    def __init__(self, responses: list) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[str, str, dict | None]] = []

    async def request(
        self, method: str, path: str, *, json: dict | None = None, params: dict | None = None
    ) -> dict:
        self.calls.append((method, path, params or json))
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    async def ensure_authenticated(self) -> None:
        pass


@pytest.fixture(autouse=True)
def _reset_adapter_registry():
    yield
    clear_adapters()


async def _make_shiprocket_integration(session: AsyncSession) -> Integration:
    integration = await IntegrationRepository(session).create(
        name="Shiprocket",
        code=IntegrationCode.SHIPROCKET,
        type=IntegrationType.COURIER,
        status=IntegrationStatus.DISCONNECTED,
        enabled=True,
    )
    await session.commit()
    return integration


async def _make_order_with_shipment(
    session: AsyncSession, *, awb: str, current_status: ShipmentStatus = ShipmentStatus.PICKED_UP
):
    order = await OrderService(session).create_order(
        actor=None,
        order_number=f"OMS-{awb}",
        customer_id=None,
        order_datetime=datetime.now(UTC),
        currency="INR",
        payment_type=PaymentType.PREPAID,
        shipping_charge=0,
        notes=None,
        items=[],
    )
    shipment = await ShipmentService(session).create_shipment(
        actor=None, order_id=order.id, awb=awb, courier_id=None, expected_delivery_date=None
    )
    await ShipmentService(session).update_shipment(
        shipment.id, actor=None, current_status=current_status
    )
    return order, shipment


def _ndr_page(*, ids: list[str], total_pages: int) -> dict:
    return {
        "data": [
            {"id": i, "awb": f"AWB{i}", "order_id": f"ord_{i}", "reason": "Customer unavailable"}
            for i in ids
        ],
        "meta": {"pagination": {"total_pages": total_pages}},
    }


def _tracking_response(
    *, status: str, event_id: int = 1, date: str = "2026-01-05 10:00:00"
) -> dict:
    return {
        "tracking_data": {
            "shipment_track_activities": [
                {
                    "id": event_id,
                    "status": status,
                    "date": date,
                    "activity": status,
                    "location": "Hub",
                }
            ]
        }
    }


# 22. SyncJob lifecycle (NDR, via the generic pipeline)
async def test_ndr_sync_creates_ndr_and_completes_job(db_session: AsyncSession) -> None:
    _, shipment = await _make_order_with_shipment(db_session, awb="AWB1")
    client = _StubClient([_ndr_page(ids=["1"], total_pages=1)])
    register_adapter(ShiprocketAdapter(client=client))
    integration = await _make_shiprocket_integration(db_session)

    service = SyncService(db_session)
    job = await service.start_sync(
        integration_id=integration.id, sync_type=SyncType.FULL, entity_type="ndr"
    )
    job = await service.execute_sync(job.id)

    assert job.status == "completed"
    assert job.records_created == 1

    total = await db_session.execute(select(func.count()).select_from(NDR))
    assert total.scalar_one() == 1


# 12/16. Duplicate NDR prevention
async def test_resyncing_the_same_ndr_updates_instead_of_duplicating(
    db_session: AsyncSession,
) -> None:
    _, shipment = await _make_order_with_shipment(db_session, awb="AWB2")
    client = _StubClient([_ndr_page(ids=["2"], total_pages=1), _ndr_page(ids=["2"], total_pages=1)])
    register_adapter(ShiprocketAdapter(client=client))
    integration = await _make_shiprocket_integration(db_session)
    service = SyncService(db_session)

    job1 = await service.run_sync(
        integration_id=integration.id, sync_type=SyncType.FULL, entity_type="ndr"
    )
    job2 = await service.run_sync(
        integration_id=integration.id, sync_type=SyncType.FULL, entity_type="ndr"
    )

    assert job1.records_created == 1
    assert job2.records_created == 0
    assert job2.records_updated == 1

    total = await db_session.execute(select(func.count()).select_from(NDR))
    assert total.scalar_one() == 1


async def test_ndr_sync_fails_gracefully_when_no_matching_shipment(
    db_session: AsyncSession,
) -> None:
    """spec §16: 'do not invent NDR data' — an NDR for an unknown AWB is
    recorded as a SyncError, not silently dropped or fabricated.
    """
    client = _StubClient([_ndr_page(ids=["99"], total_pages=1)])
    register_adapter(ShiprocketAdapter(client=client))
    integration = await _make_shiprocket_integration(db_session)

    service = SyncService(db_session)
    job = await service.run_sync(
        integration_id=integration.id, sync_type=SyncType.FULL, entity_type="ndr"
    )

    assert job.status == "partial"
    assert job.error_count == 1


# 21. Partial sync (tracking)
async def test_tracking_refresh_partial_failure_does_not_abort_other_shipments(
    db_session: AsyncSession,
) -> None:
    _, good_shipment = await _make_order_with_shipment(db_session, awb="GOOD1")
    _, bad_shipment = await _make_order_with_shipment(db_session, awb="BAD1")

    from app.integrations.shiprocket.errors import ShiprocketApiError

    responses_by_awb = {
        "GOOD1": _tracking_response(status="IN TRANSIT"),
        "BAD1": ShiprocketApiError("boom", error_type="permanent_error"),
    }

    class _AwbAwareStub:
        async def get_tracking(self, awb: str) -> dict:
            response = responses_by_awb[awb]
            if isinstance(response, Exception):
                raise response
            return response

    adapter = _AwbAwareStub()

    integration = await _make_shiprocket_integration(db_session)
    sync_service = SyncService(db_session)
    job = await sync_service.start_sync(
        integration_id=integration.id, sync_type=SyncType.FULL, entity_type="tracking"
    )
    await sync_service.mark_running(job.id)

    await refresh_tracking(db_session, job.id, adapter)
    job = await sync_service.complete_sync(job.id, success=True)

    assert job.status == "partial"
    assert job.records_failed == 1

    events = await db_session.execute(
        select(func.count())
        .select_from(ShipmentEvent)
        .where(ShipmentEvent.shipment_id == good_shipment.id)
    )
    assert events.scalar_one() == 1


# 13. RTO normalization (derived from tracking)
async def test_rto_record_is_derived_from_tracking_event(db_session: AsyncSession) -> None:
    _, shipment = await _make_order_with_shipment(
        db_session, awb="RTO001", current_status=ShipmentStatus.IN_TRANSIT
    )

    class _StubAdapter:
        async def get_tracking(self, awb: str) -> dict:
            return _tracking_response(status="RTO INITIATED")

    integration = await _make_shiprocket_integration(db_session)
    sync_service = SyncService(db_session)
    job = await sync_service.start_sync(
        integration_id=integration.id, sync_type=SyncType.FULL, entity_type="tracking"
    )

    await refresh_tracking(db_session, job.id, _StubAdapter())

    total = await db_session.execute(select(func.count()).select_from(RTO))
    assert total.scalar_one() == 1

    refreshed_shipment = await ShipmentRepository(db_session).get_by_id(shipment.id)
    assert refreshed_shipment.current_status == "rto_initiated"
    assert refreshed_shipment.rto_status == "initiated"


# 16. Duplicate tracking event prevention
async def test_tracking_refresh_does_not_duplicate_events_on_rerun(
    db_session: AsyncSession,
) -> None:
    _, shipment = await _make_order_with_shipment(db_session, awb="DUPTRACK1")

    class _StubAdapter:
        async def get_tracking(self, awb: str) -> dict:
            return _tracking_response(status="IN TRANSIT", event_id=777)

    integration = await _make_shiprocket_integration(db_session)
    sync_service = SyncService(db_session)
    job1 = await sync_service.start_sync(
        integration_id=integration.id, sync_type=SyncType.FULL, entity_type="tracking"
    )
    await refresh_tracking(db_session, job1.id, _StubAdapter())

    job2 = await sync_service.start_sync(
        integration_id=integration.id, sync_type=SyncType.FULL, entity_type="tracking"
    )
    await refresh_tracking(db_session, job2.id, _StubAdapter())

    total = await db_session.execute(
        select(func.count())
        .select_from(ShipmentEvent)
        .where(ShipmentEvent.shipment_id == shipment.id)
    )
    assert total.scalar_one() == 1


async def test_tracking_refresh_skips_terminal_shipments(db_session: AsyncSession) -> None:
    _, shipment = await _make_order_with_shipment(
        db_session, awb="DELIVERED1", current_status=ShipmentStatus.DELIVERED
    )

    class _CountingAdapter:
        def __init__(self):
            self.calls = 0

        async def get_tracking(self, awb: str) -> dict:
            self.calls += 1
            return _tracking_response(status="DELIVERED")

    integration = await _make_shiprocket_integration(db_session)
    sync_service = SyncService(db_session)
    job = await sync_service.start_sync(
        integration_id=integration.id, sync_type=SyncType.FULL, entity_type="tracking"
    )
    adapter = _CountingAdapter()
    await refresh_tracking(db_session, job.id, adapter)

    assert adapter.calls == 0

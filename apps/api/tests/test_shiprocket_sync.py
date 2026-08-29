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


# --- Shipment sync (fixes the production incident: 102/102 real NDR
# records failed with "No OMS shipment found" because nothing had ever
# imported Shiprocket's existing shipments into the OMS) ------------


def _shipments_page(*, records: list[dict], total_pages: int = 1) -> dict:
    return {"data": records, "meta": {"pagination": {"total_pages": total_pages}}}


async def _make_bare_order(session: AsyncSession, *, order_number: str):
    return await OrderService(session).create_order(
        actor=None,
        order_number=order_number,
        customer_id=None,
        order_datetime=datetime.now(UTC),
        currency="INR",
        payment_type=PaymentType.PREPAID,
        shipping_charge=0,
        notes=None,
        items=[],
    )


async def test_shipment_sync_maps_to_an_existing_order_by_channel_order_id(
    db_session: AsyncSession,
) -> None:
    order = await _make_bare_order(db_session, order_number="AWL91535")
    client = _StubClient(
        [
            _shipments_page(
                records=[
                    {
                        "id": 1536426985,
                        "channel_order_id": "AWL91535",
                        "awb": "77931116852",
                        "status": "In Transit",
                    }
                ]
            )
        ]
    )
    register_adapter(ShiprocketAdapter(client=client))
    integration = await _make_shiprocket_integration(db_session)

    service = SyncService(db_session)
    job = await service.run_sync(
        integration_id=integration.id, sync_type=SyncType.FULL, entity_type="shipments"
    )

    assert job.status == "completed"
    assert job.records_created == 1

    shipment = await ShipmentRepository(db_session).get_by_awb("77931116852")
    assert shipment is not None
    assert shipment.order_id == order.id
    assert shipment.shiprocket_shipment_id == "1536426985"
    assert shipment.current_status == ShipmentStatus.IN_TRANSIT


async def test_shipment_sync_is_idempotent_on_rerun(db_session: AsyncSession) -> None:
    await _make_bare_order(db_session, order_number="AWL91600")
    record = {"id": 42, "channel_order_id": "AWL91600", "awb": "AWB-IDEMPOTENT", "status": "New"}
    client = _StubClient(
        [_shipments_page(records=[record]), _shipments_page(records=[record])]
    )
    register_adapter(ShiprocketAdapter(client=client))
    integration = await _make_shiprocket_integration(db_session)
    service = SyncService(db_session)

    job1 = await service.run_sync(
        integration_id=integration.id, sync_type=SyncType.FULL, entity_type="shipments"
    )
    job2 = await service.run_sync(
        integration_id=integration.id, sync_type=SyncType.FULL, entity_type="shipments"
    )

    assert job1.records_created == 1
    assert job2.records_created == 0
    assert job2.records_updated == 1

    from app.models.shipment import Shipment

    total = await db_session.execute(select(func.count()).select_from(Shipment))
    assert total.scalar_one() == 1


async def test_shipment_sync_records_a_sync_error_for_an_unmatched_order_without_fabricating(
    db_session: AsyncSession,
) -> None:
    """Do not invent an OMS order id: a shipment whose `channel_order_id`
    matches no real OMS order must be recorded as a `SyncError`, and must
    never result in a `Shipment` row (fabricated or otherwise).
    """
    client = _StubClient(
        [
            _shipments_page(
                records=[
                    {"id": 1, "channel_order_id": "AWL-DOES-NOT-EXIST", "awb": "AWB-ORPHAN"}
                ]
            )
        ]
    )
    register_adapter(ShiprocketAdapter(client=client))
    integration = await _make_shiprocket_integration(db_session)

    service = SyncService(db_session)
    job = await service.run_sync(
        integration_id=integration.id, sync_type=SyncType.FULL, entity_type="shipments"
    )

    assert job.status == "partial"
    assert job.error_count == 1
    assert job.records_created == 0

    from app.models.shipment import Shipment

    total = await db_session.execute(select(func.count()).select_from(Shipment))
    assert total.scalar_one() == 0


# --- Round 13: an already-known Shipment (created via our own push
# flow, `ShiprocketOperationsService.create_shipment_for_order`) is
# resolved by (source_system, external_id) FIRST, before any
# channel_order_id/order-number lookup is even attempted -- fixes the
# real production incident where 150/150 real shipments failed with
# "channel_order_id=None" because /shipments simply has no such field,
# even though every one of those shipments already had a correct
# Shipment row in the OMS with a correct order_id. ----------------------


async def _make_order_with_existing_shiprocket_shipment(
    session: AsyncSession,
    *,
    order_number: str,
    shiprocket_shipment_id: str,
    awb: str | None = None,
    current_status: ShipmentStatus = ShipmentStatus.PENDING,
):
    """Simulates exactly what `ShiprocketOperationsService.
    create_shipment_for_order` leaves behind: a real `Shipment` row,
    already linked to a real `Order`, keyed by `(source_system=
    "shiprocket", external_id=<Shiprocket's own shipment id>)` -- the
    only way a `Shipment` is ever created in this codebase.
    """
    order = await _make_bare_order(session, order_number=order_number)
    shipment, _ = await ShipmentService(session).upsert_synced_shipment(
        source_system="shiprocket",
        external_id=shiprocket_shipment_id,
        order_id=order.id,
        shiprocket_shipment_id=shiprocket_shipment_id,
        awb=awb,
        current_status=current_status,
    )
    return order, shipment


async def test_existing_shipment_is_found_by_source_system_and_external_id(
    db_session: AsyncSession,
) -> None:
    order, shipment = await _make_order_with_existing_shiprocket_shipment(
        db_session, order_number="AWL91535", shiprocket_shipment_id="1089477745"
    )
    client = _StubClient(
        [
            _shipments_page(
                records=[
                    {
                        "id": 1089477745,
                        "channel_order_id": None,
                        "number": "",
                        "code": "",
                        "channel_name": "Shopify",
                        "awb": "",
                        "status": "PENDING",
                    }
                ]
            )
        ]
    )
    register_adapter(ShiprocketAdapter(client=client))
    integration = await _make_shiprocket_integration(db_session)

    job = await SyncService(db_session).run_sync(
        integration_id=integration.id, sync_type=SyncType.FULL, entity_type="shipments"
    )

    # 1 + 2: found and synced successfully despite every "order reference"
    # field being empty/None -- the exact real production shape.
    assert job.status == "completed"
    assert job.error_count == 0
    assert job.records_updated == 1
    assert job.records_created == 0

    from app.models.shipment import Shipment

    refreshed = await db_session.get(Shipment, shipment.id)
    # 3: existing order_id is preserved.
    assert refreshed.order_id == order.id


async def test_existing_shipment_sync_does_not_call_get_by_order_number(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """4: no OrderRepository.get_by_order_number() lookup occurs at all
    when the shipment is already known -- not just "isn't needed", but
    genuinely never called.
    """
    from app.repositories.order import OrderRepository

    await _make_order_with_existing_shiprocket_shipment(
        db_session, order_number="AWL91535", shiprocket_shipment_id="1089477745"
    )

    calls: list[str] = []
    original = OrderRepository.get_by_order_number

    async def _tracking_wrapper(self, order_number):  # noqa: ANN001, ANN202
        calls.append(order_number)
        return await original(self, order_number)

    monkeypatch.setattr(OrderRepository, "get_by_order_number", _tracking_wrapper)

    client = _StubClient(
        [_shipments_page(records=[{"id": 1089477745, "channel_order_id": None, "awb": ""}])]
    )
    register_adapter(ShiprocketAdapter(client=client))
    integration = await _make_shiprocket_integration(db_session)

    job = await SyncService(db_session).run_sync(
        integration_id=integration.id, sync_type=SyncType.FULL, entity_type="shipments"
    )

    assert job.status == "completed"
    assert calls == []


async def test_existing_shipment_sync_does_not_create_a_duplicate(
    db_session: AsyncSession,
) -> None:
    """5: syncing an already-known shipment updates the one row, never
    inserts a second.
    """
    await _make_order_with_existing_shiprocket_shipment(
        db_session, order_number="AWL91535", shiprocket_shipment_id="1089477745"
    )
    client = _StubClient(
        [_shipments_page(records=[{"id": 1089477745, "channel_order_id": None, "awb": ""}])]
    )
    register_adapter(ShiprocketAdapter(client=client))
    integration = await _make_shiprocket_integration(db_session)

    await SyncService(db_session).run_sync(
        integration_id=integration.id, sync_type=SyncType.FULL, entity_type="shipments"
    )

    from app.models.shipment import Shipment

    total = await db_session.execute(select(func.count()).select_from(Shipment))
    assert total.scalar_one() == 1


async def test_existing_shipment_fields_are_updated_from_the_shiprocket_payload(
    db_session: AsyncSession,
) -> None:
    """6: real Shiprocket fields (status here) update the existing row."""
    _, shipment = await _make_order_with_existing_shiprocket_shipment(
        db_session,
        order_number="AWL91535",
        shiprocket_shipment_id="1089477745",
        current_status=ShipmentStatus.PENDING,
    )
    client = _StubClient(
        [
            _shipments_page(
                records=[{"id": 1089477745, "channel_order_id": None, "awb": "", "status": "New"}]
            )
        ]
    )
    register_adapter(ShiprocketAdapter(client=client))
    integration = await _make_shiprocket_integration(db_session)

    await SyncService(db_session).run_sync(
        integration_id=integration.id, sync_type=SyncType.FULL, entity_type="shipments"
    )

    from app.models.shipment import Shipment

    refreshed = await db_session.get(Shipment, shipment.id)
    assert refreshed.current_status == ShipmentStatus.PENDING  # "New" maps to PENDING


async def test_existing_shipment_awb_is_populated_once_shiprocket_assigns_one(
    db_session: AsyncSession,
) -> None:
    """7: a shipment created with no AWB (real production shape for a
    PENDING shipment) gets its AWB filled in on a later sync, once
    Shiprocket actually assigns one.
    """
    order, shipment = await _make_order_with_existing_shiprocket_shipment(
        db_session, order_number="AWL91535", shiprocket_shipment_id="1089477745", awb=None
    )
    assert shipment.awb is None

    client = _StubClient(
        [
            _shipments_page(
                records=[
                    {
                        "id": 1089477745,
                        "channel_order_id": None,
                        "awb": "77931116852",
                        "status": "In Transit",
                    }
                ]
            )
        ]
    )
    register_adapter(ShiprocketAdapter(client=client))
    integration = await _make_shiprocket_integration(db_session)

    job = await SyncService(db_session).run_sync(
        integration_id=integration.id, sync_type=SyncType.FULL, entity_type="shipments"
    )
    assert job.status == "completed"

    from app.models.shipment import Shipment

    refreshed = await db_session.get(Shipment, shipment.id)
    assert refreshed.awb == "77931116852"
    assert refreshed.order_id == order.id  # still the same order, no re-resolution


async def test_shipment_sync_partial_failure_does_not_abort_other_records(
    db_session: AsyncSession,
) -> None:
    await _make_bare_order(db_session, order_number="AWL-GOOD")
    client = _StubClient(
        [
            _shipments_page(
                records=[
                    {"id": 1, "channel_order_id": "AWL-GOOD", "awb": "AWB-GOOD"},
                    {"id": 2, "channel_order_id": "AWL-MISSING", "awb": "AWB-BAD"},
                ]
            )
        ]
    )
    register_adapter(ShiprocketAdapter(client=client))
    integration = await _make_shiprocket_integration(db_session)

    service = SyncService(db_session)
    job = await service.run_sync(
        integration_id=integration.id, sync_type=SyncType.FULL, entity_type="shipments"
    )

    assert job.status == "partial"
    assert job.records_created == 1
    assert job.records_failed == 1

    shipment = await ShipmentRepository(db_session).get_by_awb("AWB-GOOD")
    assert shipment is not None


async def test_ndr_sync_succeeds_once_the_shipment_sync_has_imported_the_matching_shipment(
    db_session: AsyncSession,
) -> None:
    """The actual fix, proven end to end: an NDR for an AWB the OMS had
    never seen used to fail every time (see `test_ndr_sync_fails_
    gracefully_when_no_matching_shipment`). Once the shipment sync has
    pulled that shipment in — using only real data Shiprocket returned,
    nothing fabricated — the exact same NDR now succeeds, without the
    "No OMS shipment found" validation in `NDRService.upsert_synced_ndr`
    having been touched at all.
    """
    await _make_bare_order(db_session, order_number="AWL91535")
    shipment_client = _StubClient(
        [
            _shipments_page(
                records=[
                    {
                        "id": 1536426985,
                        "channel_order_id": "AWL91535",
                        "awb": "77931116852",
                    }
                ]
            )
        ]
    )
    register_adapter(ShiprocketAdapter(client=shipment_client))
    integration = await _make_shiprocket_integration(db_session)
    service = SyncService(db_session)

    shipment_job = await service.run_sync(
        integration_id=integration.id, sync_type=SyncType.FULL, entity_type="shipments"
    )
    assert shipment_job.status == "completed"

    ndr_client = _StubClient(
        [
            {
                "data": [
                    {
                        "id": 1540207132,
                        "shipment_id": 1536426985,
                        "channel_order_id": "AWL91535",
                        "reason": "Customer Not Available",
                        "attempts": 1,
                        "courier": "Bluedart Surface - Select 500gm",
                        "awb_code": "77931116852",
                    }
                ],
                "meta": {"pagination": {"total_pages": 1}},
            }
        ]
    )
    register_adapter(ShiprocketAdapter(client=ndr_client))

    ndr_job = await service.run_sync(
        integration_id=integration.id, sync_type=SyncType.FULL, entity_type="ndr"
    )

    assert ndr_job.status == "completed"
    assert ndr_job.records_created == 1
    assert ndr_job.error_count == 0

    total = await db_session.execute(select(func.count()).select_from(NDR))
    assert total.scalar_one() == 1


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

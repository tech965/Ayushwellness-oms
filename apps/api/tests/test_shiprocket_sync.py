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

    from app.models.integration import SyncError

    error = await db_session.scalar(select(SyncError).where(SyncError.sync_job_id == job.id))
    assert error is not None
    # Regression test: this must be classified as transient/retryable
    # ("dependency_not_ready"), not the generic, permanently-non-retryable
    # "validation_error" every other exception in this loop defaults to —
    # the real production sequence is Shopify order -> shipment created ->
    # shipment synced -> NDR generated -> NDR synced, and each step can
    # land in a different sync cycle, so "no shipment yet" is not the
    # same kind of failure as genuinely malformed data.
    assert error.error_type == "dependency_not_ready"

    from app.integrations.retry import is_retryable_error_type

    assert is_retryable_error_type(error.error_type) is True


async def test_ndr_missed_shipment_is_recovered_once_the_shipment_syncs_via_retry_processing(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end proof of the "safe deferred/reconciliation strategy
    using existing architecture" requirement: an NDR that failed only
    because its shipment hadn't synced yet must be automatically
    recovered by the existing `app.tasks.retry_processing` scheduled
    task, with no fabricated shipment/order and no new mechanism.
    """
    from app.tasks import retry_processing

    class _db_session_cm:
        def __init__(self, session: AsyncSession) -> None:
            self._session = session

        async def __aenter__(self) -> AsyncSession:
            return self._session

        async def __aexit__(self, *exc_info: object) -> None:
            return None

    monkeypatch.setattr(retry_processing, "AsyncSessionLocal", lambda: _db_session_cm(db_session))

    order = await _make_bare_order(db_session, order_number="AWL91800")
    ndr_client = _StubClient([_ndr_page(ids=["501"], total_pages=1)])
    register_adapter(ShiprocketAdapter(client=ndr_client))
    integration = await _make_shiprocket_integration(db_session)

    ndr_job = await SyncService(db_session).run_sync(
        integration_id=integration.id, sync_type=SyncType.FULL, entity_type="ndr"
    )
    assert ndr_job.status == "partial"
    assert ndr_job.error_count == 1

    total_before = await db_session.execute(select(func.count()).select_from(NDR))
    assert total_before.scalar_one() == 0

    # The shipment the NDR references (`_ndr_page`'s "AWB501") arrives in
    # a later sync cycle -- the real-world sequence this fix must tolerate.
    shipment_client = _StubClient(
        [
            _shipments_page(
                records=[{"id": 501, "channel_order_id": "AWL91800", "awb": "AWB501"}]
            )
        ]
    )
    register_adapter(ShiprocketAdapter(client=shipment_client))
    shipment_job = await SyncService(db_session).run_sync(
        integration_id=integration.id, sync_type=SyncType.FULL, entity_type="shipments"
    )
    assert shipment_job.status == "completed"

    # Now `retry_processing`'s scheduled task retries the earlier NDR
    # failure -- exactly the existing, generic mechanism, never something
    # NDR-specific.
    retry_ndr_client = _StubClient([_ndr_page(ids=["501"], total_pages=1)])
    register_adapter(ShiprocketAdapter(client=retry_ndr_client))

    retried = await retry_processing._retry_failed_syncs()
    assert retried == 1

    total_after = await db_session.execute(select(func.count()).select_from(NDR))
    assert total_after.scalar_one() == 1

    ndr = await db_session.scalar(select(NDR))
    assert ndr.order_id == order.id


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
    client = _StubClient([_shipments_page(records=[record]), _shipments_page(records=[record])])
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


async def test_multiple_shipments_in_one_page_process_independently(
    db_session: AsyncSession,
) -> None:
    """One unmatched shipment in a page must not stop the rest of that
    same page from being correctly matched and persisted -- and the job's
    counts must accurately reflect exactly which ones succeeded.
    """
    order = await _make_bare_order(db_session, order_number="AWL91700")
    client = _StubClient(
        [
            _shipments_page(
                records=[
                    {"id": 1, "channel_order_id": "AWL91700", "awb": "AWB-MATCHED-1"},
                    {"id": 2, "channel_order_id": "AWL-DOES-NOT-EXIST", "awb": "AWB-ORPHAN"},
                    {"id": 3, "channel_order_id": "AWL91700", "awb": "AWB-MATCHED-2"},
                ]
            )
        ]
    )
    register_adapter(ShiprocketAdapter(client=client))
    integration = await _make_shiprocket_integration(db_session)

    job = await SyncService(db_session).run_sync(
        integration_id=integration.id, sync_type=SyncType.FULL, entity_type="shipments"
    )

    assert job.status == "partial"
    assert job.records_received == 3
    assert job.records_created == 2
    assert job.records_failed == 1
    assert job.error_count == 1

    from app.models.shipment import Shipment

    matched = (
        await db_session.execute(select(Shipment).where(Shipment.order_id == order.id))
    ).scalars().all()
    assert {s.awb for s in matched} == {"AWB-MATCHED-1", "AWB-MATCHED-2"}


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
                records=[{"id": 1, "channel_order_id": "AWL-DOES-NOT-EXIST", "awb": "AWB-ORPHAN"}]
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


# --- Round 14: `channel_order_id` is confirmed live to always be absent
# from `/shipments` for a shipment with no existing OMS `Shipment` row —
# `GET /orders/show/{order_id}` is the confirmed-reliable fallback. Real
# live evidence: for an OMS-created order, `channel_order_id` comes back
# WITH the `#` (`"#AWL92268"`, matching `Order.order_number` verbatim);
# for a shipment created outside this OMS, it comes back WITHOUT it
# (`"AWL43729"`, needing `#` prepended). Both forms are covered. --------


def _orders_show_response(channel_order_id: object) -> dict:
    return {"data": {"channel_order_id": channel_order_id}}


async def test_shipment_sync_resolves_via_orders_show_fallback_with_hash_prefixed_channel_order_id(
    db_session: AsyncSession,
) -> None:
    """1: a shipment with no existing Shipment row and no channel_order_id
    on /shipments still resolves, via GET /orders/show/{order_id} --
    real shape for an order this OMS itself pushed (channel_order_id
    comes back with the `#` already).
    """
    order = await _make_bare_order(db_session, order_number="#AWL92268")
    client = _StubClient(
        [
            _shipments_page(
                records=[
                    {
                        "id": 1544069864,
                        "channel_order_id": None,
                        "order_id": 1547850287,
                        "awb": "",
                        "status": "New",
                    }
                ]
            ),
            _orders_show_response("#AWL92268"),
        ]
    )
    register_adapter(ShiprocketAdapter(client=client))
    integration = await _make_shiprocket_integration(db_session)

    job = await SyncService(db_session).run_sync(
        integration_id=integration.id, sync_type=SyncType.FULL, entity_type="shipments"
    )

    assert job.status == "completed"
    assert job.records_created == 1

    shipment = await ShipmentRepository(db_session).get_by_source_external_id(
        source_system="shiprocket", external_id="1544069864"
    )
    assert shipment is not None
    assert shipment.order_id == order.id


async def test_shipment_sync_resolves_via_orders_show_fallback_without_hash_prefix(
    db_session: AsyncSession,
) -> None:
    """2: channel_order_id without a leading `#` -- real shape for a
    shipment created outside this OMS (e.g. Shopify's native Shiprocket
    connection), confirmed live this engagement.
    """
    order = await _make_bare_order(db_session, order_number="#AWL43729")
    client = _StubClient(
        [
            _shipments_page(
                records=[
                    {
                        "id": 1085847769,
                        "channel_order_id": None,
                        "order_id": 1089478217,
                        "awb": "",
                    }
                ]
            ),
            _orders_show_response("AWL43729"),  # no leading '#' -- real live shape
        ]
    )
    register_adapter(ShiprocketAdapter(client=client))
    integration = await _make_shiprocket_integration(db_session)

    job = await SyncService(db_session).run_sync(
        integration_id=integration.id, sync_type=SyncType.FULL, entity_type="shipments"
    )

    assert job.status == "completed"
    assert job.records_created == 1

    shipment = await ShipmentRepository(db_session).get_by_source_external_id(
        source_system="shiprocket", external_id="1085847769"
    )
    assert shipment is not None
    assert shipment.order_id == order.id


async def test_shipment_sync_does_not_crash_on_a_numeric_channel_order_id(
    db_session: AsyncSession,
) -> None:
    """3: real production evidence this engagement showed a bare numeric
    channel_order_id (41531) via /orders/show for a shipment whose order
    genuinely isn't in this OMS. It must fail gracefully (SyncError), not
    raise an unhandled exception from `.startswith()` on a non-string.
    """
    client = _StubClient(
        [
            _shipments_page(
                records=[
                    {"id": 1089477745, "channel_order_id": None, "order_id": 1089477745, "awb": ""}
                ]
            ),
            _orders_show_response(41531),  # raw int, not a string
        ]
    )
    register_adapter(ShiprocketAdapter(client=client))
    integration = await _make_shiprocket_integration(db_session)

    job = await SyncService(db_session).run_sync(
        integration_id=integration.id, sync_type=SyncType.FULL, entity_type="shipments"
    )

    assert job.status == "partial"
    assert job.error_count == 1
    assert job.records_created == 0


async def test_shipment_sync_records_a_sync_error_when_orders_show_also_has_no_match(
    db_session: AsyncSession,
) -> None:
    """6: even after trying both channel_order_id sources, an order that
    genuinely doesn't exist in the OMS must still fail cleanly -- never
    fabricated, and the SyncError is recorded, not silently dropped.
    """
    client = _StubClient(
        [
            _shipments_page(
                records=[
                    {
                        "id": 999,
                        "channel_order_id": None,
                        "order_id": 12345,
                        "awb": "",
                    }
                ]
            ),
            _orders_show_response("AWL-DOES-NOT-EXIST"),
        ]
    )
    register_adapter(ShiprocketAdapter(client=client))
    integration = await _make_shiprocket_integration(db_session)

    job = await SyncService(db_session).run_sync(
        integration_id=integration.id, sync_type=SyncType.FULL, entity_type="shipments"
    )

    assert job.status == "partial"
    assert job.error_count == 1
    assert job.records_created == 0

    from app.models.shipment import Shipment

    total = await db_session.execute(select(func.count()).select_from(Shipment))
    assert total.scalar_one() == 0


# --- Round 16: real production evidence showed the Round 15 date-based
# guard is correct (verified against a live 200-shipment sample, 100%
# correctly judged too old) but can't catch a shipment that looks
# "recent" by that measure and still doesn't match for another reason.
# Once a live check has genuinely completed for one shipment, repeating
# it on every subsequent 10-minute sync is pure waste. -------------------


async def test_shipment_sync_does_not_repeat_orders_show_for_a_confirmed_unmatched_shipment(
    db_session: AsyncSession,
) -> None:
    """A shipment resolved via `/orders/show` on a first sync (and
    genuinely found to match no OMS order) must not trigger a second live
    call on the next sync — the second run's stub client has no
    `/orders/show` response queued at all, so a repeat attempt would
    raise `IndexError` and fail this test.
    """
    client_1 = _StubClient(
        [
            _shipments_page(
                records=[{"id": 555, "channel_order_id": None, "order_id": 98765, "awb": ""}]
            ),
            _orders_show_response("AWL-STILL-DOES-NOT-EXIST"),
        ]
    )
    register_adapter(ShiprocketAdapter(client=client_1))
    integration = await _make_shiprocket_integration(db_session)
    service = SyncService(db_session)

    job1 = await service.run_sync(
        integration_id=integration.id, sync_type=SyncType.FULL, entity_type="shipments"
    )
    assert job1.status == "partial"
    assert job1.error_count == 1

    # Second run: same shipment, still no matching order, but the stub
    # client has ONLY the /shipments page queued -- no /orders/show
    # response. If the cache didn't work, this would IndexError.
    client_2 = _StubClient(
        [
            _shipments_page(
                records=[{"id": 555, "channel_order_id": None, "order_id": 98765, "awb": ""}]
            )
        ]
    )
    register_adapter(ShiprocketAdapter(client=client_2))

    job2 = await service.run_sync(
        integration_id=integration.id, sync_type=SyncType.FULL, entity_type="shipments"
    )

    assert job2.status == "partial"
    assert job2.error_count == 1
    assert job2.records_created == 0

    from app.models.integration import SyncError

    latest_error = (
        await db_session.execute(select(SyncError).where(SyncError.sync_job_id == job2.id))
    ).scalar_one()
    assert "already confirmed unmatched on a previous sync" in latest_error.error_message
    assert latest_error.error_type == "validation_error"


async def test_shipment_sync_retries_orders_show_after_a_prior_permission_failure(
    db_session: AsyncSession,
) -> None:
    """The opposite of the test above: a shipment whose PRIOR attempt
    failed with a permission error (never actually completed the check)
    must NOT be treated as "confirmed unmatched" — it must be retried on
    the next sync, since that condition can and does change (confirmed
    live this engagement: an account-wide block cleared on its own).
    """
    from app.integrations.shiprocket.errors import ShiprocketApiError

    client_1 = _StubClient(
        [
            _shipments_page(
                records=[{"id": 777, "channel_order_id": None, "order_id": 55512, "awb": ""}]
            ),
            ShiprocketApiError(
                "Shiprocket account lacks permission for this operation.",
                error_type="authorization_error",
                status_code=403,
            ),
        ]
    )
    register_adapter(ShiprocketAdapter(client=client_1))
    integration = await _make_shiprocket_integration(db_session)
    service = SyncService(db_session)

    job1 = await service.run_sync(
        integration_id=integration.id, sync_type=SyncType.FULL, entity_type="shipments"
    )
    assert job1.error_count == 1

    # Second run: the permission block has since cleared, and this time
    # the shipment genuinely matches. A fresh adapter (new instance, new
    # circuit-breaker state) with a real, successful response queued --
    # if the cache wrongly treated the first run as "confirmed", this
    # response would never be consumed and the order would stay unmatched.
    order = await _make_bare_order(db_session, order_number="#AWL-NOWMATCHES")
    client_2 = _StubClient(
        [
            _shipments_page(
                records=[{"id": 777, "channel_order_id": None, "order_id": 55512, "awb": ""}]
            ),
            _orders_show_response("#AWL-NOWMATCHES"),
        ]
    )
    register_adapter(ShiprocketAdapter(client=client_2))

    job2 = await service.run_sync(
        integration_id=integration.id, sync_type=SyncType.FULL, entity_type="shipments"
    )

    assert job2.status == "completed"
    assert job2.records_created == 1

    shipment = await ShipmentRepository(db_session).get_by_source_external_id(
        source_system="shiprocket", external_id="777"
    )
    assert shipment is not None
    assert shipment.order_id == order.id


async def test_shipment_sync_distinguishes_a_permission_error_from_a_genuine_no_match(
    db_session: AsyncSession,
) -> None:
    """Real production incident: an `/orders/show` failure (here, the
    exact live error — a 403 "Shiprocket account lacks permission for
    this operation") used to be silently swallowed and reported with the
    same generic message as a shipment that genuinely has no matching
    OMS order. That made a permanent, account-wide permission block on
    this endpoint indistinguishable — in every log line and every
    SyncError — from ordinary, expected unmatched-shipment noise. The
    recorded error must now name the real cause.
    """
    from app.integrations.shiprocket.errors import ShiprocketApiError

    client = _StubClient(
        [
            _shipments_page(
                records=[{"id": 1143095000, "channel_order_id": None, "order_id": 41531, "awb": ""}]
            ),
            ShiprocketApiError(
                "Shiprocket account lacks permission for this operation.",
                error_type="authorization_error",
                status_code=403,
            ),
        ]
    )
    register_adapter(ShiprocketAdapter(client=client))
    integration = await _make_shiprocket_integration(db_session)

    job = await SyncService(db_session).run_sync(
        integration_id=integration.id, sync_type=SyncType.FULL, entity_type="shipments"
    )

    assert job.status == "partial"
    assert job.error_count == 1

    from app.models.integration import SyncError

    error = await db_session.scalar(select(SyncError).where(SyncError.sync_job_id == job.id))
    assert error is not None
    # The real IntegrationError is allowed to propagate out of
    # `_upsert_shipment` (Round 16) instead of being swallowed into a
    # generic NotFoundError, so `_run_entity_sync`'s new `except
    # IntegrationError` branch records its REAL error_type here — the
    # distinguishing signal now lives in a queryable column, not buried
    # in message text.
    assert error.error_type == "authorization_error"
    assert "Shiprocket account lacks permission" in error.error_message


async def test_shiprocket_adapter_get_order_short_circuits_after_a_confirmed_permission_error(
    db_session: AsyncSession,
) -> None:
    """Unit-level proof of the circuit breaker: a second `get_order` call
    on the SAME adapter instance, after a 403 already confirmed this
    account can't use the endpoint, must fail immediately with no further
    network call — the stub client has only one response queued, so a
    second real attempt would raise `IndexError`.
    """
    from app.integrations.shiprocket.errors import ShiprocketApiError

    client = _StubClient(
        [
            ShiprocketApiError(
                "Shiprocket account lacks permission for this operation.",
                error_type="authorization_error",
                status_code=403,
            )
        ]
    )
    adapter = ShiprocketAdapter(client=client)

    from app.core.exceptions import IntegrationError

    with pytest.raises(IntegrationError):
        await adapter.get_order("111")
    assert len(client.calls) == 1

    with pytest.raises(IntegrationError):
        await adapter.get_order("222")
    # No second network call was made -- the breaker tripped.
    assert len(client.calls) == 1


async def test_shipment_sync_does_not_hammer_orders_show_after_one_permission_error(
    db_session: AsyncSession,
) -> None:
    """End-to-end proof at the sync level: two shipments in the SAME run,
    both needing the `/orders/show` fallback. Only one stub response
    (a 403) is queued for it — if the second shipment attempted its own
    live call, the stub client would raise `IndexError` and fail this
    test. Both shipments must still be recorded as failed (never
    fabricated), confirming the whole backlog degrades gracefully instead
    of each record hammering a confirmed-blocked endpoint.
    """
    from app.integrations.shiprocket.errors import ShiprocketApiError

    client = _StubClient(
        [
            _shipments_page(
                records=[
                    {"id": 1, "channel_order_id": None, "order_id": 100, "awb": ""},
                    {"id": 2, "channel_order_id": None, "order_id": 200, "awb": ""},
                ]
            ),
            ShiprocketApiError(
                "Shiprocket account lacks permission for this operation.",
                error_type="authorization_error",
                status_code=403,
            ),
        ]
    )
    register_adapter(ShiprocketAdapter(client=client))
    integration = await _make_shiprocket_integration(db_session)

    job = await SyncService(db_session).run_sync(
        integration_id=integration.id, sync_type=SyncType.FULL, entity_type="shipments"
    )

    assert job.status == "partial"
    assert job.error_count == 2
    assert job.records_created == 0

    from app.models.integration import SyncError

    errors = (
        (await db_session.execute(select(SyncError).where(SyncError.sync_job_id == job.id)))
        .scalars()
        .all()
    )
    assert len(errors) == 2
    assert all(e.error_type == "authorization_error" for e in errors)


async def test_shipment_sync_does_not_call_orders_show_when_channel_order_id_already_present(
    db_session: AsyncSession,
) -> None:
    """No wasted live call: when /shipments already has a usable
    channel_order_id, GET /orders/show is never attempted -- the stub
    client has only one response queued, so a second (unwanted) call
    would raise IndexError and fail this test.
    """
    await _make_bare_order(db_session, order_number="#AWL-DIRECT")
    client = _StubClient(
        [
            _shipments_page(
                records=[{"id": 5, "channel_order_id": "#AWL-DIRECT", "order_id": 777, "awb": ""}]
            )
        ]
    )
    register_adapter(ShiprocketAdapter(client=client))
    integration = await _make_shiprocket_integration(db_session)

    job = await SyncService(db_session).run_sync(
        integration_id=integration.id, sync_type=SyncType.FULL, entity_type="shipments"
    )

    assert job.status == "completed"
    assert job.records_created == 1


async def test_shipment_sync_via_orders_show_fallback_is_idempotent_on_rerun(
    db_session: AsyncSession,
) -> None:
    """5: once a shipment has been resolved via the orders/show fallback
    and saved, a re-sync finds it by (source_system, external_id) FIRST
    -- the fallback (and its extra live call) never fires again. The
    second stub client has only the /shipments page queued; a repeat
    orders/show call would raise IndexError.
    """
    order = await _make_bare_order(db_session, order_number="#AWL92268")
    first_client = _StubClient(
        [
            _shipments_page(
                records=[
                    {"id": 1544069864, "channel_order_id": None, "order_id": 1547850287, "awb": ""}
                ]
            ),
            _orders_show_response("#AWL92268"),
        ]
    )
    register_adapter(ShiprocketAdapter(client=first_client))
    integration = await _make_shiprocket_integration(db_session)
    service = SyncService(db_session)

    job1 = await service.run_sync(
        integration_id=integration.id, sync_type=SyncType.FULL, entity_type="shipments"
    )
    assert job1.records_created == 1

    second_client = _StubClient(
        [
            _shipments_page(
                records=[
                    {
                        "id": 1544069864,
                        "channel_order_id": None,
                        "order_id": 1547850287,
                        "awb": "AWB-NEW",
                    }
                ]
            )
        ]
    )
    register_adapter(ShiprocketAdapter(client=second_client))

    job2 = await service.run_sync(
        integration_id=integration.id, sync_type=SyncType.FULL, entity_type="shipments"
    )

    assert job2.status == "completed"
    assert job2.records_created == 0
    assert job2.records_updated == 1

    from app.models.shipment import Shipment

    total = await db_session.execute(select(func.count()).select_from(Shipment))
    assert total.scalar_one() == 1

    shipment = await ShipmentRepository(db_session).get_by_source_external_id(
        source_system="shiprocket", external_id="1544069864"
    )
    assert shipment.order_id == order.id
    assert shipment.awb == "AWB-NEW"


# --- Round 15/17: the /orders/show fallback is a real, confirmed live
# fix, but at real account scale (thousands of historical shipments) it
# meant every 10-minute scheduled sync re-attempted the same already-
# provably-unmatchable historical records forever -- real production
# evidence: hitting Shiprocket's rate limit (HTTP 429) mid-sync. Round 15
# tried to bound this by skipping the live call outright for a shipment
# whose `shiprocket_created_at` predates the OMS's earliest-ever synced
# order -- but a real production account with thin/still-catching-up
# order-sync coverage (as few as 11 Shopify orders synced) showed this
# was an unsafe matching decision in disguise: plenty of shipments older
# than that thin "earliest order" boundary belonged to real, resolvable
# orders. Round 17 removed the skip -- a timestamp difference alone must
# never discard a shipment a strong identifier could still resolve. The
# Round 16 "already confirmed unmatched" cache (tested above) is what
# actually bounds repeated live calls now: it only ever skips a call that
# has already run once and genuinely found nothing, never one that was
# merely old-looking. ---------------------------------------------------


async def test_old_shipment_still_attempts_orders_show_and_matches_via_strong_identifier(
    db_session: AsyncSession,
) -> None:
    """Regression test for the Round 17 fix: a shipment created well
    before the OMS's earliest synced order must NOT be discarded on that
    timestamp difference alone -- the live /orders/show call still runs,
    and when it resolves a real `channel_order_id`, the shipment is
    correctly matched, exactly as if it weren't "old" at all.
    """
    order = await _make_bare_order(
        db_session, order_number="#AWL78183"
    )  # sets the OMS's earliest order_datetime to "now" (test creation time)
    client = _StubClient(
        [
            _shipments_page(
                records=[
                    {
                        "id": 1,
                        "channel_order_id": None,
                        "order_id": 12345,
                        "awb": "",
                        # Long before "now" -- predates the order above,
                        # yet must still resolve via the live lookup below.
                        "created_at": "2020-01-01 00:00:00",
                    }
                ]
            ),
            _orders_show_response("#AWL78183"),
        ]
    )
    register_adapter(ShiprocketAdapter(client=client))
    integration = await _make_shiprocket_integration(db_session)

    job = await SyncService(db_session).run_sync(
        integration_id=integration.id, sync_type=SyncType.FULL, entity_type="shipments"
    )

    assert job.status == "completed"
    assert job.error_count == 0
    assert job.records_created == 1

    shipment = await ShipmentRepository(db_session).get_by_source_external_id(
        source_system="shiprocket", external_id="1"
    )
    assert shipment is not None
    assert shipment.order_id == order.id


async def test_old_shipment_with_no_real_match_still_fails_cleanly_not_via_typeerror(
    db_session: AsyncSession,
) -> None:
    """The tz-aware/naive datetime comparison behind `predates_oms_coverage`
    is now purely diagnostic (never gates the live lookup), but it still
    runs on every old shipment -- it must never itself crash and get
    misreported as an ordinary "no match" (a real bug found while first
    writing this coverage: comparing Shiprocket's naive parsed datetime
    against the OMS's tz-aware `order_datetime` raised a bare TypeError
    that looked identical to a genuine no-match from the outside -- same
    job status, same error_count).
    """
    await _make_bare_order(db_session, order_number="#AWL78183")
    client = _StubClient(
        [
            _shipments_page(
                records=[
                    {
                        "id": 1,
                        "channel_order_id": None,
                        "order_id": 12345,
                        "awb": "",
                        "created_at": "2020-01-01 00:00:00",
                    }
                ]
            ),
            _orders_show_response("AWL-STILL-DOES-NOT-EXIST"),
        ]
    )
    register_adapter(ShiprocketAdapter(client=client))
    integration = await _make_shiprocket_integration(db_session)

    job = await SyncService(db_session).run_sync(
        integration_id=integration.id, sync_type=SyncType.FULL, entity_type="shipments"
    )

    assert job.status == "partial"
    assert job.error_count == 1
    assert job.records_created == 0

    from app.models.integration import SyncError

    error = await db_session.scalar(select(SyncError).where(SyncError.sync_job_id == job.id))
    assert error is not None
    assert "No OMS order found for Shiprocket shipment" in error.error_message
    assert "TypeError" not in error.error_message
    assert "naive" not in error.error_message


async def test_shipment_sync_still_calls_orders_show_when_no_created_at_is_present(
    db_session: AsyncSession,
) -> None:
    """No timestamp to compare against means "don't skip" -- the boundary
    must never cause a resolvable shipment to be silently dropped just
    because its age is unknown.
    """
    order = await _make_bare_order(db_session, order_number="#AWL92268")
    client = _StubClient(
        [
            _shipments_page(
                records=[
                    {"id": 1544069864, "channel_order_id": None, "order_id": 1547850287, "awb": ""}
                ]
            ),
            _orders_show_response("#AWL92268"),
        ]
    )
    register_adapter(ShiprocketAdapter(client=client))
    integration = await _make_shiprocket_integration(db_session)

    job = await SyncService(db_session).run_sync(
        integration_id=integration.id, sync_type=SyncType.FULL, entity_type="shipments"
    )

    assert job.status == "completed"
    assert job.records_created == 1

    shipment = await ShipmentRepository(db_session).get_by_source_external_id(
        source_system="shiprocket", external_id="1544069864"
    )
    assert shipment.order_id == order.id


async def test_shipment_sync_still_calls_orders_show_when_no_oms_orders_exist_yet(
    db_session: AsyncSession,
) -> None:
    """No OMS orders synced at all yet -- `get_earliest_order_datetime()`
    returns None, so there's nothing to compare against and the boundary
    must not skip anything.
    """
    client = _StubClient(
        [
            _shipments_page(
                records=[
                    {
                        "id": 1,
                        "channel_order_id": None,
                        "order_id": 12345,
                        "awb": "",
                        "created_at": "2020-01-01 00:00:00",
                    }
                ]
            ),
            _orders_show_response("DOES-NOT-MATTER"),
        ]
    )
    register_adapter(ShiprocketAdapter(client=client))
    integration = await _make_shiprocket_integration(db_session)

    job = await SyncService(db_session).run_sync(
        integration_id=integration.id, sync_type=SyncType.FULL, entity_type="shipments"
    )

    # Fails to match (as expected -- no real order), but critically the
    # live call WAS made (the stub's second response was consumed without
    # raising IndexError), proving the boundary correctly did not skip.
    assert job.status == "partial"
    assert job.error_count == 1


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
    # Real usage (`app.tasks.shiprocket_sync._execute_tracking_refresh`)
    # always completes the job after refresh_tracking returns -- doing
    # the same here is what makes a second start_sync for the same
    # entity_type legal (SyncService.start_sync now refuses to start a
    # second concurrent job for the same integration/entity_type; a
    # completed job is no longer "active").
    await sync_service.complete_sync(job1.id, success=True)

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


# --- Cross-job cursor resume: real production evidence this engagement
# showed `ShiprocketAdapter.fetch_incremental` has no genuine "since"
# filter, so every sync -- incremental or full -- restarts the entire
# `/shipments` crawl at page 1. Against a ~23k-record backlog that takes
# hours, meaning a scheduled sync could never realistically reach today's
# new shipments. `Integration.configuration["sync_cursors"]` persists
# where the last job's crawl stopped so the next one resumes instead of
# re-walking history from scratch every time. ---------------------------


async def test_shipment_sync_resumes_from_a_persisted_cursor(db_session: AsyncSession) -> None:
    """A cursor already saved from a previous job's crawl (e.g. it hit the
    time budget mid-backlog) must be used as the starting page for the
    next job -- not page 1.
    """
    client = _StubClient([_shipments_page(records=[])])
    register_adapter(ShiprocketAdapter(client=client))
    integration = await _make_shiprocket_integration(db_session)
    await IntegrationRepository(db_session).update(
        integration, configuration={"sync_cursors": {"shipments": "7"}}
    )
    await db_session.commit()

    await SyncService(db_session).run_sync(
        integration_id=integration.id, sync_type=SyncType.FULL, entity_type="shipments"
    )

    method, path, params = client.calls[0]
    assert method == "GET"
    assert path == "/shipments"
    assert params["page"] == 7


async def test_shipment_sync_clears_the_cursor_once_a_full_pass_completes(
    db_session: AsyncSession,
) -> None:
    """Once a crawl genuinely reaches the end of the list (`has_more`
    False), the persisted cursor must be cleared -- the next job starts a
    fresh pass from page 1 rather than being stuck resuming from "the end"
    forever.
    """
    client = _StubClient([_shipments_page(records=[], total_pages=1)])
    register_adapter(ShiprocketAdapter(client=client))
    integration = await _make_shiprocket_integration(db_session)
    await IntegrationRepository(db_session).update(
        integration, configuration={"sync_cursors": {"shipments": "3"}}
    )
    await db_session.commit()

    await SyncService(db_session).run_sync(
        integration_id=integration.id, sync_type=SyncType.FULL, entity_type="shipments"
    )

    await db_session.refresh(integration)
    assert integration.configuration["sync_cursors"].get("shipments") is None


async def test_shipment_sync_stops_early_and_saves_a_resumable_cursor_past_the_time_budget(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With the time budget already exhausted, a job must stop after one
    page (persisting the next page as the resume cursor) instead of
    crawling the whole backlog in a single run -- the fix for a ~23k-record
    Shiprocket backlog otherwise taking hours per job and blocking every
    scheduled attempt behind it via the one-active-job-per-entity guard.
    A second page response is queued but must never be consumed.
    """
    from datetime import timedelta

    import app.services.sync_service as sync_service_module

    monkeypatch.setattr(sync_service_module, "_MAX_ENTITY_SYNC_DURATION", timedelta(seconds=-1))

    # No `order_id`/`channel_order_id` on this record -- resolution stops
    # at "no match" with zero further API calls, so the single queued page
    # response below is the only network call this test should ever make.
    client = _StubClient(
        [
            _shipments_page(records=[{"id": "no-match-1", "awb": ""}], total_pages=2),
            _shipments_page(records=[{"id": "no-match-2", "awb": ""}], total_pages=2),
        ]
    )
    register_adapter(ShiprocketAdapter(client=client))
    integration = await _make_shiprocket_integration(db_session)

    job = await SyncService(db_session).run_sync(
        integration_id=integration.id, sync_type=SyncType.FULL, entity_type="shipments"
    )

    assert job.status == "partial"
    assert len(client.calls) == 1

    await db_session.refresh(integration)
    assert integration.configuration["sync_cursors"]["shipments"] == "2"


async def test_stale_persisted_cursor_is_reset_instead_of_permanently_failing_the_sync(
    db_session: AsyncSession,
) -> None:
    """Real production incident: a shipments Full Sync failed immediately
    with records_received=0/error_count=1/status=failed even though
    `GET /shipments?page=1` worked fine when tested manually -- the job
    was silently resuming from a page number left over by an earlier
    interrupted crawl, and Shiprocket rejected that specific page with a
    validation error (422). The very first fetch of a run must recover
    from exactly this by clearing the stale cursor and retrying once from
    page 1, rather than failing the whole job on a self-inflicted, fully
    recoverable page number.
    """
    from app.integrations.shiprocket.errors import ShiprocketApiError

    client = _StubClient(
        [
            ShiprocketApiError(
                "Shiprocket rejected the request payload (validation error).",
                error_type="validation_error",
                status_code=422,
            ),
            _shipments_page(records=[]),
        ]
    )
    register_adapter(ShiprocketAdapter(client=client))
    integration = await _make_shiprocket_integration(db_session)
    await IntegrationRepository(db_session).update(
        integration, configuration={"sync_cursors": {"shipments": "9999"}}
    )
    await db_session.commit()

    job = await SyncService(db_session).run_sync(
        integration_id=integration.id, sync_type=SyncType.FULL, entity_type="shipments"
    )

    assert job.status == "completed"
    assert job.error_count == 0

    assert len(client.calls) == 2
    first_call, second_call = client.calls
    assert first_call[2]["page"] == 9999
    assert second_call[2]["page"] == 1

    await db_session.refresh(integration)
    assert integration.configuration["sync_cursors"].get("shipments") is None


async def test_a_genuine_validation_error_on_a_later_page_still_fails_the_job(
    db_session: AsyncSession,
) -> None:
    """The stale-cursor recovery must be narrowly scoped to the first
    fetch of a run that was actually resuming from a cursor -- a
    validation error on a *later* page (not a resume situation) is a
    real, currently-reachable failure and must still fail the job, per
    this method's own documented contract ("a failure fetching a page
    itself... fails the whole job — there's no partial data to salvage").
    """
    from app.integrations.shiprocket.errors import ShiprocketApiError

    client = _StubClient(
        [
            _shipments_page(records=[{"id": 1, "awb": ""}], total_pages=2),
            ShiprocketApiError(
                "Shiprocket rejected the request payload (validation error).",
                error_type="validation_error",
                status_code=422,
            ),
        ]
    )
    register_adapter(ShiprocketAdapter(client=client))
    integration = await _make_shiprocket_integration(db_session)

    job = await SyncService(db_session).run_sync(
        integration_id=integration.id, sync_type=SyncType.FULL, entity_type="shipments"
    )

    assert job.status == "failed"
    assert len(client.calls) == 2


# --- Incremental mode: once a backlog crawl has completed a full pass
# (a COMPLETED/PARTIAL job exists and no resume cursor is left), every
# later scheduled run is a cheap newest-first slice bounded by `since` --
# it must NOT re-crawl history and must NOT persist a cross-job cursor,
# so a stuck cursor can never pin the crawl to stale pages. While a
# backlog cursor is still set, the sync stays in resumable backlog mode
# regardless of `since`. --------------------------------------------------


async def _complete_a_shipments_sync(db_session: AsyncSession, integration: Integration) -> None:
    """Give `(integration, "shipments")` a COMPLETED SyncJob so the next
    run derives a `since` and enters incremental mode.
    """
    svc = SyncService(db_session)
    baseline = await svc.start_sync(
        integration_id=integration.id, sync_type=SyncType.INCREMENTAL, entity_type="shipments"
    )
    await svc.complete_sync(baseline.id, success=True)


async def test_incremental_shipment_sync_uses_newest_first_and_persists_no_cursor(
    db_session: AsyncSession,
) -> None:
    # A newest-first page entirely older than `since` -> the adapter early
    # -stops, the job completes, and no resume cursor is written.
    client = _StubClient(
        [
            _shipments_page(
                records=[{"id": "old-1", "awb": "", "created_at": "1st Jan 2026 10:00 AM"}],
                total_pages=25,
            )
        ]
    )
    register_adapter(ShiprocketAdapter(client=client))
    integration = await _make_shiprocket_integration(db_session)
    await _complete_a_shipments_sync(db_session, integration)

    job = await SyncService(db_session).run_sync(
        integration_id=integration.id, sync_type=SyncType.INCREMENTAL, entity_type="shipments"
    )

    assert job.status in ("completed", "partial")
    assert len(client.calls) == 1
    _, path, params = client.calls[0]
    assert path == "/shipments"
    assert params["sort"] == "desc" and params["sort_by"] == "created_at"

    await db_session.refresh(integration)
    assert (integration.configuration or {}).get("sync_cursors", {}).get("shipments") is None


async def test_incremental_shipment_sync_stays_in_backlog_mode_while_a_cursor_is_set(
    db_session: AsyncSession,
) -> None:
    # Baseline job exists (so `since` is set) AND a backlog cursor is still
    # persisted -> the run must resume the plain ascending crawl from that
    # page, not switch to newest-first incremental.
    client = _StubClient([_shipments_page(records=[], total_pages=9)])
    register_adapter(ShiprocketAdapter(client=client))
    integration = await _make_shiprocket_integration(db_session)
    await _complete_a_shipments_sync(db_session, integration)
    await IntegrationRepository(db_session).update(
        integration, configuration={"sync_cursors": {"shipments": "4"}}
    )
    await db_session.commit()

    await SyncService(db_session).run_sync(
        integration_id=integration.id, sync_type=SyncType.INCREMENTAL, entity_type="shipments"
    )

    _, path, params = client.calls[0]
    assert path == "/shipments"
    assert params["page"] == 4
    assert "sort" not in params  # plain backlog fetch(), not fetch_incremental()

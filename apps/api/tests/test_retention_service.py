"""API-first / minimal-storage initiative: the raw-payload retention
service and its admin endpoint.

Phase 1 confirms the dry-run default reports without writing, a real
run only clears `raw_external_payload` (never any other column, never
the row), only rows past the cutoff are touched, and the endpoint is
RBAC-gated.

Phase 2 (production verification) adds: `shipments` is proven excluded
because `shiprocket_order_id()` has a real live dependency on that
column; already-null rows are proven ignored (not just "not an error");
repeated real execution is proven idempotent; and a real (non-dry-run)
execution is proven audit-logged.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from app.models.audit_log import AuditLog
from app.models.enums import FulfillmentStatus, OrderStatus, PaymentStatus, PaymentType
from app.models.order import Order
from app.models.shipment import Shipment
from app.services.retention_service import (
    RETAINABLE_TABLES,
    RetentionError,
    null_out_stale_raw_payloads,
)
from app.services.shiprocket_service import shiprocket_order_id
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio


async def _make_synced_order(
    session: AsyncSession, *, order_number: str, updated_at: datetime
) -> Order:
    order = Order(
        order_number=order_number,
        order_datetime=datetime.now(UTC) - timedelta(days=5),
        currency="INR",
        subtotal=Decimal("500.00"),
        total_amount=Decimal("500.00"),
        payment_type=PaymentType.PREPAID,
        payment_status=PaymentStatus.PAID,
        status=OrderStatus.CONFIRMED,
        fulfillment_status=FulfillmentStatus.UNFULFILLED,
        source_system="shopify",
        external_id=order_number,
        raw_external_payload={"id": order_number, "big": "x" * 200},
    )
    session.add(order)
    await session.flush()
    # `updated_at` has an `onupdate`/default-now server trigger on insert —
    # overwrite it directly so the cutoff math in the service under test
    # is deterministic, same technique other tests in this suite use for
    # backdating sync/age-sensitive rows.
    await session.execute(
        Order.__table__.update().where(Order.id == order.id).values(updated_at=updated_at)
    )
    await session.commit()
    await session.refresh(order)
    return order


async def test_dry_run_reports_without_writing(db_session: AsyncSession) -> None:
    old_order = await _make_synced_order(
        db_session, order_number="OLD-1", updated_at=datetime.now(UTC) - timedelta(days=400)
    )

    count = await null_out_stale_raw_payloads(
        db_session, table="orders", older_than_days=90, dry_run=True
    )

    assert count == 1
    refreshed = (
        await db_session.execute(select(Order).where(Order.id == old_order.id))
    ).scalar_one()
    assert refreshed.raw_external_payload is not None


async def test_real_run_only_clears_old_rows_payload_column(db_session: AsyncSession) -> None:
    old_order = await _make_synced_order(
        db_session, order_number="OLD-2", updated_at=datetime.now(UTC) - timedelta(days=400)
    )
    recent_order = await _make_synced_order(
        db_session, order_number="RECENT-1", updated_at=datetime.now(UTC) - timedelta(days=5)
    )
    old_id, recent_id = old_order.id, recent_order.id

    count = await null_out_stale_raw_payloads(
        db_session, table="orders", older_than_days=90, dry_run=False
    )
    await db_session.commit()
    # The bulk `UPDATE` runs with `synchronize_session=False` (required for
    # a dialect-portable bulk statement) -- it never touches the identity
    # map, so an object already loaded in this session (like the two
    # created above) keeps its stale in-memory attributes until expired.
    # Ids are captured above, before expiring, since even a plain
    # attribute read on an expired object is a sync lazy-load under
    # `AsyncSession` and raises `MissingGreenlet` outside an `await`.
    db_session.expire_all()

    assert count == 1
    refreshed_old = (
        await db_session.execute(select(Order).where(Order.id == old_id))
    ).scalar_one()
    refreshed_recent = (
        await db_session.execute(select(Order).where(Order.id == recent_id))
    ).scalar_one()

    assert refreshed_old.raw_external_payload is None
    # Every normalized column is untouched -- only the JSON blob cleared.
    assert refreshed_old.order_number == "OLD-2"
    assert refreshed_old.source_system == "shopify"
    assert refreshed_old.external_id == "OLD-2"
    assert refreshed_old.status == OrderStatus.CONFIRMED
    # The recent row is unaffected entirely.
    assert refreshed_recent.raw_external_payload is not None


async def test_unknown_table_raises(db_session: AsyncSession) -> None:
    with pytest.raises(RetentionError):
        await null_out_stale_raw_payloads(
            db_session, table="not_a_real_table", older_than_days=90, dry_run=True
        )


async def test_storage_health_requires_permission(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    client = await make_authenticated_client(db_session, permission_codes=["orders.read"])
    async with client:
        response = await client.get("/api/v1/admin/retention/storage-health")
    assert response.status_code == 403


async def test_storage_health_shape_against_sqlite_test_db(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    """Under the SQLite test dialect, the Postgres-only catalog fields
    come back empty/`None` rather than erroring; the append-only section
    (plain `func.count`/`func.min`/`func.max` aggregates, portable to
    both dialects) still reports real numbers.
    """
    await _make_synced_order(
        db_session, order_number="STORAGE-HEALTH-1", updated_at=datetime.now(UTC)
    )
    client = await make_authenticated_client(db_session, permission_codes=["integrations.manage"])
    async with client:
        response = await client.get("/api/v1/admin/retention/storage-health")
    assert response.status_code == 200
    body = response.json()["data"]
    assert body["dialect"] == "sqlite"
    assert body["database_size_mb"] is None
    assert body["largest_tables"] == []
    append_only_tables = {row["table"] for row in body["append_only_tables"]}
    assert append_only_tables == {
        "order_events",
        "shipment_events",
        "webhook_events",
        "audit_logs",
        "payment_transactions",
        "sync_jobs",
        "sync_errors",
    }
    for row in body["append_only_tables"]:
        assert row["row_count"] >= 0
        assert row["older_than_90d"] >= 0


async def test_endpoint_requires_permission(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    client = await make_authenticated_client(db_session, permission_codes=["orders.read"])
    async with client:
        response = await client.post(
            "/api/v1/admin/retention/raw-payloads/run",
            json={"table": "orders", "older_than_days": 90, "dry_run": True},
        )
    assert response.status_code == 403


async def test_endpoint_dry_run_default_true(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    await _make_synced_order(
        db_session, order_number="OLD-3", updated_at=datetime.now(UTC) - timedelta(days=400)
    )
    client = await make_authenticated_client(
        db_session, permission_codes=["integrations.manage"]
    )
    async with client:
        response = await client.post(
            "/api/v1/admin/retention/raw-payloads/run",
            json={"table": "orders", "older_than_days": 90},
        )
    assert response.status_code == 200
    body = response.json()["data"]
    assert body["dry_run"] is True
    assert body["rows_affected"] == 1

    refreshed = (
        await db_session.execute(select(Order).where(Order.order_number == "OLD-3"))
    ).scalar_one()
    assert refreshed.raw_external_payload is not None


async def test_shipments_excluded_because_shiprocket_order_id_depends_on_raw_payload(
    db_session: AsyncSession,
) -> None:
    """Regression test for the Phase 2 dependency audit:
    `app.services.shiprocket_service.shiprocket_order_id()` reads
    `Shipment.raw_external_payload["order_id"]` directly -- no other
    column stores Shiprocket's real numeric order id -- and that id
    drives the Fulfillment Queue's "Ship Order"/"Process Shipment"
    clipboard-copy action. If `shipments` were ever added back to
    `RETAINABLE_TABLES`, running retention against it would silently
    make that action unusable for cleared rows. First confirms the
    table is excluded, then proves *why* by exercising the real
    dependency end to end.
    """
    assert "shipments" not in RETAINABLE_TABLES

    order = Order(
        order_number="SHIP-DEP-1",
        order_datetime=datetime.now(UTC) - timedelta(days=5),
        currency="INR",
        subtotal=Decimal("500.00"),
        total_amount=Decimal("500.00"),
        payment_type=PaymentType.PREPAID,
        payment_status=PaymentStatus.PAID,
        status=OrderStatus.CONFIRMED,
        fulfillment_status=FulfillmentStatus.UNFULFILLED,
    )
    db_session.add(order)
    await db_session.flush()

    shipment = Shipment(
        order_id=order.id,
        raw_external_payload={"order_id": 1900007001, "shipment_id": 7001},
    )
    db_session.add(shipment)
    await db_session.commit()
    await db_session.refresh(shipment)

    assert shiprocket_order_id(shipment) == "1900007001"

    # Simulating what WOULD happen if `shipments` were retainable --
    # clearing the payload makes the real order id unrecoverable.
    shipment.raw_external_payload = None
    assert shiprocket_order_id(shipment) is None


async def test_already_null_payloads_are_ignored_not_recounted(db_session: AsyncSession) -> None:
    # The column is genuinely SQL NULL whenever it's never assigned at
    # all (the real "never synced" case for every manually created
    # order) -- omitted here deliberately, NOT passed as an explicit
    # `raw_external_payload=None` kwarg: SQLAlchemy's plain `JSON` type
    # (the non-Postgres side of `JSONType`, used by this SQLite test
    # suite) encodes an explicit Python `None` as the JSON `"null"`
    # literal rather than SQL NULL unless the column sets
    # `none_as_null=True` (it doesn't, matching production Postgres/
    # JSONB's own `None`-as-JSON-null behavior) -- that would make this
    # test assert the wrong thing.
    order = Order(
        order_number="ALREADY-NULL-1",
        order_datetime=datetime.now(UTC) - timedelta(days=5),
        currency="INR",
        subtotal=Decimal("500.00"),
        total_amount=Decimal("500.00"),
        payment_type=PaymentType.PREPAID,
        payment_status=PaymentStatus.PAID,
        status=OrderStatus.CONFIRMED,
        fulfillment_status=FulfillmentStatus.UNFULFILLED,
        source_system="shopify",
        external_id="already-null-1",
    )
    db_session.add(order)
    await db_session.flush()
    await db_session.execute(
        Order.__table__.update()
        .where(Order.id == order.id)
        .values(updated_at=datetime.now(UTC) - timedelta(days=400))
    )
    await db_session.commit()

    dry_run_count = await null_out_stale_raw_payloads(
        db_session, table="orders", older_than_days=90, dry_run=True
    )
    real_count = await null_out_stale_raw_payloads(
        db_session, table="orders", older_than_days=90, dry_run=False
    )
    await db_session.commit()

    assert dry_run_count == 0
    assert real_count == 0


async def test_repeated_real_execution_is_idempotent(db_session: AsyncSession) -> None:
    await _make_synced_order(
        db_session, order_number="IDEMPOTENT-1", updated_at=datetime.now(UTC) - timedelta(days=400)
    )

    first_run = await null_out_stale_raw_payloads(
        db_session, table="orders", older_than_days=90, dry_run=False
    )
    await db_session.commit()
    second_run = await null_out_stale_raw_payloads(
        db_session, table="orders", older_than_days=90, dry_run=False
    )
    await db_session.commit()

    assert first_run == 1
    assert second_run == 0


async def test_endpoint_rejects_non_positive_older_than_days(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    client = await make_authenticated_client(db_session, permission_codes=["integrations.manage"])
    async with client:
        response = await client.post(
            "/api/v1/admin/retention/raw-payloads/run",
            json={"table": "orders", "older_than_days": 0, "dry_run": True},
        )
    assert response.status_code == 422


async def test_real_execution_is_audit_logged_dry_run_is_not(
    db_session: AsyncSession, make_authenticated_client
) -> None:
    await _make_synced_order(
        db_session, order_number="AUDIT-1", updated_at=datetime.now(UTC) - timedelta(days=400)
    )
    client = await make_authenticated_client(db_session, permission_codes=["integrations.manage"])

    async with client:
        dry_run_response = await client.post(
            "/api/v1/admin/retention/raw-payloads/run",
            json={"table": "orders", "older_than_days": 90, "dry_run": True},
        )
        assert dry_run_response.status_code == 200

        dry_run_logs = (
            (
                await db_session.execute(
                    select(AuditLog).where(AuditLog.action == "retention.raw_payloads_cleared")
                )
            )
            .scalars()
            .all()
        )
        assert len(dry_run_logs) == 0

        real_response = await client.post(
            "/api/v1/admin/retention/raw-payloads/run",
            json={"table": "orders", "older_than_days": 90, "dry_run": False},
        )
        assert real_response.status_code == 200

    real_logs = (
        await db_session.execute(
            select(AuditLog).where(AuditLog.action == "retention.raw_payloads_cleared")
        )
    ).scalars().all()
    assert len(real_logs) == 1
    assert real_logs[0].new_value["rows_affected"] == 1

"""ReconciliationService — Phase 2.4.

Architecture (spec §12): Provider -> Fetch -> Normalize -> Compare ->
`ReconciliationResult` -> Persist/Report -> no automatic corrective
action. This module is the *only* place reconciliation logic lives —
adapters are only ever called through their existing public methods
(`fetch`/`normalize`/`get_tracking`), never modified for this purpose.

Every check is bounded to `_SAMPLE_LIMIT` records — this reconciles a
recent working sample each run, not a full-catalog diff, to avoid
loading entire datasets into memory or hammering a rate-limited provider
(spec §27). A provider that isn't registered or isn't configured makes
its checks report as "skipped" in `ReconciliationRun.run_metadata` —
never fabricated, same honesty rule `health_check()` already follows.
`ReconciliationService` never writes to a business table; it only ever
reports.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import IntegrationError, NotFoundError
from app.integrations.base import IntegrationAdapter
from app.integrations.registry import get_adapter
from app.integrations.shiprocket.adapter import ShiprocketAdapter
from app.integrations.shiprocket.normalizer import TRACKING_NORMALIZER, extract_tracking_events
from app.models.audit_log import AuditLog
from app.models.auth import User
from app.models.enums import (
    ReconciliationRunStatus,
    ReconciliationStatus,
    RTOStatus,
    ShipmentStatus,
)
from app.models.integration import IntegrationCode
from app.models.mixins import SourceSystem
from app.models.ndr import NDR
from app.models.order import Order
from app.models.reconciliation import ReconciliationResult, ReconciliationRun
from app.models.rto import RTO
from app.models.shipment import Shipment
from app.repositories.customer import CustomerRepository
from app.repositories.order import OrderRepository
from app.repositories.product import ProductRepository
from app.repositories.reconciliation import (
    ReconciliationResultRepository,
    ReconciliationRunRepository,
)
from app.repositories.shipment import ShipmentRepository
from app.schemas.common import PageParams, SortParams
from app.services.audit_service import AuditService

_SAMPLE_LIMIT = 25

# Mirrors app.integrations.shiprocket.sync's derivation — RTO has no
# confirmed independent Shiprocket endpoint (see docs/integrations/shiprocket.md),
# so both the sync path and this reconciliation check derive it from the
# same tracking-status mapping rather than inventing a second source.
_RTO_STATUS_FROM_TRACKING: dict[ShipmentStatus, RTOStatus] = {
    ShipmentStatus.RTO_INITIATED: RTOStatus.INITIATED,
    ShipmentStatus.RTO_DELIVERED: RTOStatus.RECEIVED,
}


class ReconciliationService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.runs = ReconciliationRunRepository(session)
        self.results = ReconciliationResultRepository(session)
        self.orders = OrderRepository(session)
        self.products = ProductRepository(session)
        self.customers = CustomerRepository(session)
        self.shipments = ShipmentRepository(session)
        self.audit = AuditService(session)

    async def _get_run(self, run_id: uuid.UUID) -> ReconciliationRun:
        run = await self.runs.get_by_id(run_id)
        if run is None:
            raise NotFoundError("Reconciliation run not found.")
        return run

    # --- Lifecycle --------------------------------------------------------

    async def start_run(self, *, actor: User | None) -> ReconciliationRun:
        run = await self.runs.create(
            triggered_by_user_id=actor.id if actor else None,
            status=ReconciliationRunStatus.RUNNING,
            started_at=datetime.now(UTC),
        )
        await self.audit.record(
            user=actor,
            action="reconciliation.started",
            entity_type="reconciliation_run",
            entity_id=str(run.id),
        )
        await self.session.commit()
        return run

    async def run_checks(self, run_id: uuid.UUID) -> ReconciliationRun:
        shopify_adapter = get_adapter(IntegrationCode.SHOPIFY)
        shiprocket_adapter = get_adapter(IntegrationCode.SHIPROCKET)

        skipped: list[str] = []
        errored: list[str] = []

        checks: list[tuple[str, str, str, Callable[..., Awaitable[None]], tuple[Any, ...]]] = [
            (
                "oms_order_missing_shopify_id",
                "shopify",
                "order",
                self._check_oms_orders_missing_shopify_id,
                (),
            ),
            (
                "shipment_missing_shiprocket_id",
                "shiprocket",
                "shipment",
                self._check_shipment_missing_shiprocket_id,
                (),
            ),
            (
                "shiprocket_shipment_missing_in_oms",
                "shiprocket",
                "shipment",
                self._check_shiprocket_shipment_missing_in_oms,
                (),
            ),
        ]

        if shopify_adapter is not None:
            checks.extend(
                [
                    (
                        "shopify_order_missing_in_oms",
                        "shopify",
                        "order",
                        self._check_shopify_orders_missing_in_oms,
                        (shopify_adapter,),
                    ),
                    (
                        "shopify_product_diff",
                        "shopify",
                        "product",
                        self._check_shopify_product_diff,
                        (shopify_adapter,),
                    ),
                    (
                        "shopify_customer_diff",
                        "shopify",
                        "customer",
                        self._check_shopify_customer_diff,
                        (shopify_adapter,),
                    ),
                ]
            )
        else:
            skipped.extend(
                ["shopify_order_missing_in_oms", "shopify_product_diff", "shopify_customer_diff"]
            )

        if isinstance(shiprocket_adapter, ShiprocketAdapter):
            checks.extend(
                [
                    (
                        "shiprocket_tracking_family",
                        "shiprocket",
                        "shipment",
                        self._check_shiprocket_tracking_family,
                        (shiprocket_adapter,),
                    ),
                    (
                        "shiprocket_ndr_mismatch",
                        "shiprocket",
                        "ndr",
                        self._check_ndr_mismatch,
                        (shiprocket_adapter,),
                    ),
                ]
            )
        else:
            skipped.extend(["shiprocket_tracking_family", "shiprocket_ndr_mismatch"])

        for check_type, provider, entity_type, fn, args in checks:
            outcome = await self._safe_check(run_id, check_type, provider, entity_type, fn, *args)
            if outcome == "skipped":
                skipped.append(check_type)
            elif outcome == "error":
                errored.append(check_type)

        return await self._complete_run(run_id, skipped=skipped, errored=errored)

    async def fail_run(self, run_id: uuid.UUID, *, message: str) -> ReconciliationRun:
        run = await self._get_run(run_id)
        await self.runs.update(
            run,
            status=ReconciliationRunStatus.FAILED,
            completed_at=datetime.now(UTC),
            run_metadata={"error": message},
        )
        await self.audit.record(
            user=None,
            action="reconciliation.failed",
            entity_type="reconciliation_run",
            entity_id=str(run.id),
            new_value={"error": message},
        )
        await self.session.commit()
        return run

    async def _complete_run(
        self, run_id: uuid.UUID, *, skipped: list[str], errored: list[str]
    ) -> ReconciliationRun:
        run = await self._get_run(run_id)
        stmt = (
            select(ReconciliationResult.status, func.count())
            .where(ReconciliationResult.run_id == run_id)
            .group_by(ReconciliationResult.status)
        )
        counts: dict[ReconciliationStatus, int] = dict.fromkeys(ReconciliationStatus, 0)
        for status, count in (await self.session.execute(stmt)).all():
            counts[ReconciliationStatus(status)] = count
        total = sum(counts.values())

        await self.runs.update(
            run,
            status=ReconciliationRunStatus.COMPLETED,
            completed_at=datetime.now(UTC),
            total_checked=total,
            reconciled_count=counts[ReconciliationStatus.RECONCILED],
            mismatch_count=counts[ReconciliationStatus.MISMATCH],
            missing_count=counts[ReconciliationStatus.MISSING],
            error_count=counts[ReconciliationStatus.ERROR],
            run_metadata={"skipped_checks": skipped, "errored_checks": errored},
        )
        await self.audit.record(
            user=None,
            action="reconciliation.completed",
            entity_type="reconciliation_run",
            entity_id=str(run.id),
            new_value={
                "total_checked": total,
                "mismatch_count": counts[ReconciliationStatus.MISMATCH],
                "missing_count": counts[ReconciliationStatus.MISSING],
                "error_count": counts[ReconciliationStatus.ERROR],
            },
        )
        await self.session.commit()
        return run

    async def _safe_check(
        self,
        run_id: uuid.UUID,
        check_type: str,
        provider: str,
        entity_type: str,
        fn: Callable[..., Awaitable[None]],
        *args: Any,
    ) -> str:
        """Runs one check; a failure here must never abort the rest of the
        run (same "one bad record doesn't fail the job" rule `SyncService`
        already follows). Returns "ok" / "skipped" / "error".
        """
        try:
            await fn(run_id, *args)
            await self.session.commit()
            return "ok"
        except IntegrationError as exc:
            await self.session.rollback()
            if exc.details.get("error_type") == "not_configured":
                return "skipped"
            await self.results.create(
                run_id=run_id,
                check_type=check_type,
                provider=provider,
                entity_type=entity_type,
                status=ReconciliationStatus.ERROR,
                message=exc.message[:1000],
            )
            await self.session.commit()
            return "error"
        except Exception as exc:  # noqa: BLE001 - one failing check must not abort the run
            await self.session.rollback()
            await self.results.create(
                run_id=run_id,
                check_type=check_type,
                provider=provider,
                entity_type=entity_type,
                status=ReconciliationStatus.ERROR,
                message=str(exc)[:1000],
            )
            await self.session.commit()
            return "error"

    async def _record(
        self,
        run_id: uuid.UUID,
        *,
        check_type: str,
        provider: str,
        entity_type: str,
        status: ReconciliationStatus,
        internal_id: str | None = None,
        external_id: str | None = None,
        expected_value: dict[str, Any] | None = None,
        actual_value: dict[str, Any] | None = None,
        message: str | None = None,
    ) -> None:
        await self.results.create(
            run_id=run_id,
            check_type=check_type,
            provider=provider,
            entity_type=entity_type,
            internal_id=internal_id,
            external_id=external_id,
            expected_value=expected_value,
            actual_value=actual_value,
            status=status,
            message=message,
        )

    # --- Pure-DB checks (no provider call, always run) --------------------

    async def _check_oms_orders_missing_shopify_id(self, run_id: uuid.UUID) -> None:
        """Spec §11 item 2 — an OMS order sourced from Shopify but missing
        the domain-specific `shopify_order_id` (structural inconsistency,
        not a live provider comparison)."""
        stmt = (
            select(Order)
            .where(Order.source_system == SourceSystem.SHOPIFY, Order.shopify_order_id.is_(None))
            .limit(_SAMPLE_LIMIT)
        )
        orders = (await self.session.execute(stmt)).scalars().all()
        for order in orders:
            await self._record(
                run_id,
                check_type="oms_order_missing_shopify_id",
                provider="shopify",
                entity_type="order",
                internal_id=str(order.id),
                external_id=order.external_id,
                status=ReconciliationStatus.MISMATCH,
                message="Order is sourced from Shopify but shopify_order_id is not set.",
            )

    async def _check_shipment_missing_shiprocket_id(self, run_id: uuid.UUID) -> None:
        """Spec §11 item 5."""
        stmt = (
            select(Shipment)
            .where(
                Shipment.source_system == SourceSystem.SHIPROCKET,
                Shipment.shiprocket_shipment_id.is_(None),
            )
            .limit(_SAMPLE_LIMIT)
        )
        shipments = (await self.session.execute(stmt)).scalars().all()
        for shipment in shipments:
            await self._record(
                run_id,
                check_type="shipment_missing_shiprocket_id",
                provider="shiprocket",
                entity_type="shipment",
                internal_id=str(shipment.id),
                external_id=shipment.external_id,
                status=ReconciliationStatus.MISMATCH,
                message=(
                    "Shipment is sourced from Shiprocket but shiprocket_shipment_id is not set."
                ),
            )

    async def _check_shiprocket_shipment_missing_in_oms(self, run_id: uuid.UUID) -> None:
        """Spec §11 item 6. No bulk "list every Shiprocket shipment"
        endpoint was confirmed (see docs/integrations/shiprocket.md), so
        this is a best-effort self-consistency check via the audit trail:
        every successful "created a Shiprocket shipment" audit entry must
        resolve to a current `Shipment` row.
        """
        stmt = (
            select(AuditLog)
            .where(AuditLog.action == "shipment.created_via_shiprocket")
            .order_by(AuditLog.created_at.desc())
            .limit(_SAMPLE_LIMIT)
        )
        logs = (await self.session.execute(stmt)).scalars().all()
        for log in logs:
            try:
                shipment_id = uuid.UUID(log.entity_id)
            except ValueError:
                continue
            shipment = await self.shipments.get_by_id(shipment_id)
            if shipment is None:
                await self._record(
                    run_id,
                    check_type="shiprocket_shipment_missing_in_oms",
                    provider="shiprocket",
                    entity_type="shipment",
                    internal_id=log.entity_id,
                    status=ReconciliationStatus.MISSING,
                    message=(
                        "Audit log recorded a Shiprocket shipment creation but no "
                        "matching Shipment row exists."
                    ),
                )

    # --- Shopify-backed checks ----------------------------------------

    async def _check_shopify_orders_missing_in_oms(
        self, run_id: uuid.UUID, adapter: IntegrationAdapter
    ) -> None:
        """Spec §11 item 1."""
        page = await adapter.fetch("orders", limit=_SAMPLE_LIMIT)
        for raw in page.nodes:
            normalized = adapter.normalize("orders", raw)
            external_id = normalized.get("external_id")
            if not external_id:
                continue
            existing = await self.orders.get_by_source_external_id(
                source_system=SourceSystem.SHOPIFY, external_id=external_id
            )
            status = ReconciliationStatus.RECONCILED if existing else ReconciliationStatus.MISSING
            await self._record(
                run_id,
                check_type="shopify_order_missing_in_oms",
                provider="shopify",
                entity_type="order",
                internal_id=str(existing.id) if existing else None,
                external_id=external_id,
                status=status,
                message=None if existing else "Shopify order has no matching OMS order.",
            )

    async def _check_shopify_product_diff(
        self, run_id: uuid.UUID, adapter: IntegrationAdapter
    ) -> None:
        """Spec §11 item 3. Only compares products already matched by
        external id — an unmatched Shopify product is a sync gap, not a
        diff, and out of scope for this check.
        """
        page = await adapter.fetch("products", limit=_SAMPLE_LIMIT)
        for raw in page.nodes:
            normalized = adapter.normalize("products", raw)
            external_id = normalized.get("external_id")
            if not external_id:
                continue
            existing = await self.products.get_by_source_external_id(
                source_system=SourceSystem.SHOPIFY, external_id=external_id
            )
            if existing is None:
                continue
            diffs: dict[str, Any] = {}
            if existing.title != normalized.get("title"):
                diffs["title"] = {"oms": existing.title, "shopify": normalized.get("title")}
            shopify_status = normalized.get("status")
            shopify_status_value = shopify_status.value if shopify_status is not None else None
            if existing.status.value != shopify_status_value:
                diffs["status"] = {
                    "oms": existing.status.value,
                    "shopify": shopify_status_value,
                }
            if (existing.vendor or None) != (normalized.get("vendor") or None):
                diffs["vendor"] = {"oms": existing.vendor, "shopify": normalized.get("vendor")}
            status = ReconciliationStatus.MISMATCH if diffs else ReconciliationStatus.RECONCILED
            await self._record(
                run_id,
                check_type="shopify_product_diff",
                provider="shopify",
                entity_type="product",
                internal_id=str(existing.id),
                external_id=external_id,
                expected_value={"shopify": diffs} if diffs else None,
                status=status,
                message=None if not diffs else f"Fields differ: {', '.join(diffs)}.",
            )

    async def _check_shopify_customer_diff(
        self, run_id: uuid.UUID, adapter: IntegrationAdapter
    ) -> None:
        """Spec §11 item 4."""
        page = await adapter.fetch("customers", limit=_SAMPLE_LIMIT)
        for raw in page.nodes:
            normalized = adapter.normalize("customers", raw)
            external_id = normalized.get("external_id")
            if not external_id:
                continue
            existing = await self.customers.get_by_source_external_id(
                source_system=SourceSystem.SHOPIFY, external_id=external_id
            )
            if existing is None:
                continue
            diffs: dict[str, Any] = {}
            if (existing.email or None) != (normalized.get("email") or None):
                diffs["email"] = {"oms": existing.email, "shopify": normalized.get("email")}
            if (existing.first_name or None) != (normalized.get("first_name") or None):
                diffs["first_name"] = {
                    "oms": existing.first_name,
                    "shopify": normalized.get("first_name"),
                }
            if (existing.last_name or None) != (normalized.get("last_name") or None):
                diffs["last_name"] = {
                    "oms": existing.last_name,
                    "shopify": normalized.get("last_name"),
                }
            status = ReconciliationStatus.MISMATCH if diffs else ReconciliationStatus.RECONCILED
            await self._record(
                run_id,
                check_type="shopify_customer_diff",
                provider="shopify",
                entity_type="customer",
                internal_id=str(existing.id),
                external_id=external_id,
                expected_value={"shopify": diffs} if diffs else None,
                status=status,
                message=None if not diffs else f"Fields differ: {', '.join(diffs)}.",
            )

    # --- Shiprocket-backed checks --------------------------------------

    async def _check_shiprocket_tracking_family(
        self, run_id: uuid.UUID, adapter: ShiprocketAdapter
    ) -> None:
        """Spec §11 items 7 (AWB), 8 (courier), 9 (tracking status), 11
        (RTO) — combined into one pass per shipment so each sampled
        shipment costs exactly one live `get_tracking` call.
        """
        stmt = (
            select(Shipment)
            .where(Shipment.awb.is_not(None), Shipment.shiprocket_shipment_id.is_not(None))
            .options(selectinload(Shipment.courier))
            .order_by(Shipment.updated_at.desc())
            .limit(_SAMPLE_LIMIT)
        )
        shipments = (await self.session.execute(stmt)).scalars().all()

        for shipment in shipments:
            assert shipment.awb is not None  # guaranteed by the query filter above
            raw = await adapter.get_tracking(shipment.awb)
            tracking_data = (
                raw.get("tracking_data") if isinstance(raw.get("tracking_data"), dict) else raw
            )
            provider_awb = tracking_data.get("awb") if isinstance(tracking_data, dict) else None
            if provider_awb:
                awb_status = (
                    ReconciliationStatus.RECONCILED
                    if provider_awb == shipment.awb
                    else ReconciliationStatus.MISMATCH
                )
                await self._record(
                    run_id,
                    check_type="awb_mismatch",
                    provider="shiprocket",
                    entity_type="shipment",
                    internal_id=str(shipment.id),
                    external_id=shipment.shiprocket_shipment_id,
                    expected_value={"awb": provider_awb},
                    actual_value={"awb": shipment.awb},
                    status=awb_status,
                    message=(
                        None
                        if awb_status == ReconciliationStatus.RECONCILED
                        else "AWB on file differs from Shiprocket's tracking response."
                    ),
                )

            events = extract_tracking_events(raw)
            if not events:
                continue
            normalized = TRACKING_NORMALIZER.normalize_event(events[-1])

            provider_courier = normalized.get("courier_name")
            if provider_courier:
                oms_courier = shipment.courier.name if shipment.courier else None
                courier_status = (
                    ReconciliationStatus.RECONCILED
                    if oms_courier == provider_courier
                    else ReconciliationStatus.MISMATCH
                )
                await self._record(
                    run_id,
                    check_type="courier_mismatch",
                    provider="shiprocket",
                    entity_type="shipment",
                    internal_id=str(shipment.id),
                    external_id=shipment.shiprocket_shipment_id,
                    expected_value={"courier": provider_courier},
                    actual_value={"courier": oms_courier},
                    status=courier_status,
                    message=(
                        None
                        if courier_status == ReconciliationStatus.RECONCILED
                        else "Courier on file differs from Shiprocket's latest tracking event."
                    ),
                )

            mapped_status = normalized.get("mapped_status")
            if mapped_status is None:
                continue
            status_match = (
                ReconciliationStatus.RECONCILED
                if mapped_status == shipment.current_status
                else ReconciliationStatus.MISMATCH
            )
            await self._record(
                run_id,
                check_type="tracking_status_mismatch",
                provider="shiprocket",
                entity_type="shipment",
                internal_id=str(shipment.id),
                external_id=shipment.shiprocket_shipment_id,
                expected_value={"status": mapped_status.value},
                actual_value={"status": shipment.current_status.value},
                status=status_match,
                message=(
                    None
                    if status_match == ReconciliationStatus.RECONCILED
                    else "OMS shipment status is stale relative to Shiprocket — a tracking "
                    "refresh should resolve this."
                ),
            )

            rto_status = _RTO_STATUS_FROM_TRACKING.get(mapped_status)
            if rto_status is None:
                continue
            existing_rto = (
                await self.session.execute(select(RTO).where(RTO.shipment_id == shipment.id))
            ).scalar_one_or_none()
            if existing_rto is None:
                await self._record(
                    run_id,
                    check_type="rto_mismatch",
                    provider="shiprocket",
                    entity_type="rto",
                    internal_id=str(shipment.id),
                    external_id=shipment.shiprocket_shipment_id,
                    expected_value={"rto_status": rto_status.value},
                    status=ReconciliationStatus.MISSING,
                    message="Latest tracking indicates RTO but no RTO record exists in OMS.",
                )
            else:
                rto_match = (
                    ReconciliationStatus.RECONCILED
                    if existing_rto.status == rto_status
                    else ReconciliationStatus.MISMATCH
                )
                await self._record(
                    run_id,
                    check_type="rto_mismatch",
                    provider="shiprocket",
                    entity_type="rto",
                    internal_id=str(existing_rto.id),
                    external_id=shipment.shiprocket_shipment_id,
                    expected_value={"rto_status": rto_status.value},
                    actual_value={"rto_status": existing_rto.status.value},
                    status=rto_match,
                    message=(
                        None
                        if rto_match == ReconciliationStatus.RECONCILED
                        else (
                            "RTO status in OMS differs from what the latest tracking event implies."
                        )
                    ),
                )

    async def _check_ndr_mismatch(self, run_id: uuid.UUID, adapter: ShiprocketAdapter) -> None:
        """Spec §11 item 10. Only compares NDRs matched by AWB to an
        existing OMS shipment — an unmatched AWB is a data-integrity
        problem `NDRService.upsert_synced_ndr` already refuses to invent
        (raises `NotFoundError` during sync), not a reconciliation mismatch.
        """
        page = await adapter.fetch("ndr", limit=_SAMPLE_LIMIT)
        for raw in page.nodes:
            normalized = adapter.normalize("ndr", raw)
            awb = normalized.get("awb")
            if not awb:
                continue
            shipment = await self.shipments.get_by_awb(awb)
            if shipment is None:
                continue
            existing_ndr = (
                (await self.session.execute(select(NDR).where(NDR.shipment_id == shipment.id)))
                .scalars()
                .first()
            )
            if existing_ndr is None:
                await self._record(
                    run_id,
                    check_type="ndr_mismatch",
                    provider="shiprocket",
                    entity_type="ndr",
                    internal_id=str(shipment.id),
                    external_id=awb,
                    expected_value={"reason": normalized.get("external_reason")},
                    status=ReconciliationStatus.MISSING,
                    message=(
                        "Shiprocket reports an NDR for this AWB but no NDR record exists in OMS."
                    ),
                )
                continue
            diffs: dict[str, Any] = {}
            if (existing_ndr.external_reason or None) != (
                normalized.get("external_reason") or None
            ):
                diffs["reason"] = {
                    "oms": existing_ndr.external_reason,
                    "shiprocket": normalized.get("external_reason"),
                }
            if existing_ndr.attempt_number != normalized.get("attempt_number"):
                diffs["attempt_number"] = {
                    "oms": existing_ndr.attempt_number,
                    "shiprocket": normalized.get("attempt_number"),
                }
            status = ReconciliationStatus.MISMATCH if diffs else ReconciliationStatus.RECONCILED
            await self._record(
                run_id,
                check_type="ndr_mismatch",
                provider="shiprocket",
                entity_type="ndr",
                internal_id=str(existing_ndr.id),
                external_id=awb,
                expected_value={"shiprocket": diffs} if diffs else None,
                status=status,
                message=None if not diffs else f"Fields differ: {', '.join(diffs)}.",
            )

    # --- Read/resolve API ------------------------------------------------

    async def list_runs(
        self, *, page_params: PageParams, sort_params: SortParams
    ) -> tuple[list[ReconciliationRun], int]:
        items, total = await self.runs.list(page_params=page_params, sort_params=sort_params)
        return list(items), total

    async def get_run(self, run_id: uuid.UUID) -> ReconciliationRun:
        return await self._get_run(run_id)

    async def list_results(
        self,
        *,
        page_params: PageParams,
        sort_params: SortParams,
        run_id: uuid.UUID | None = None,
        status: str | None = None,
        check_type: str | None = None,
        provider: str | None = None,
        resolved: bool | None = None,
    ) -> tuple[list[ReconciliationResult], int]:
        query = self.results.search_query(
            run_id=run_id,
            status=status,
            check_type=check_type,
            provider=provider,
            resolved=resolved,
        )
        items, total = await self.results.list(
            page_params=page_params, sort_params=sort_params, query=query
        )
        return list(items), total

    async def resolve_result(self, result_id: uuid.UUID, *, actor: User) -> ReconciliationResult:
        result = await self.results.get_by_id(result_id)
        if result is None:
            raise NotFoundError("Reconciliation result not found.")
        await self.results.update(
            result,
            resolved=True,
            resolved_at=datetime.now(UTC),
            resolved_by_user_id=actor.id,
        )
        await self.audit.record(
            user=actor,
            action="reconciliation.result_resolved",
            entity_type="reconciliation_result",
            entity_id=str(result.id),
            new_value={"check_type": result.check_type, "status": result.status.value},
        )
        await self.session.commit()
        return result

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.core.logging import get_logger
from app.models.auth import User
from app.models.ndr import NDR
from app.repositories.ndr import NDRRepository
from app.repositories.shipment import ShipmentRepository
from app.schemas.common import PageParams, SortParams
from app.services.audit_service import AuditService

logger = get_logger(__name__)


class NDRService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.ndrs = NDRRepository(session)
        self.shipments = ShipmentRepository(session)
        self.audit = AuditService(session)

    async def list_ndrs(
        self,
        *,
        page_params: PageParams,
        sort_params: SortParams,
        status: str | None = None,
        courier_id: uuid.UUID | None = None,
    ) -> tuple[list[NDR], int]:
        query = self.ndrs.search_query(status=status, courier_id=courier_id)
        items, total = await self.ndrs.list(
            page_params=page_params, sort_params=sort_params, query=query
        )
        return list(items), total

    async def get_ndr(self, ndr_id: uuid.UUID) -> NDR:
        ndr = await self.ndrs.get_by_id(ndr_id)
        if ndr is None:
            raise NotFoundError("NDR not found.")
        return ndr

    async def update_ndr(
        self, ndr_id: uuid.UUID, *, actor: User | None, **fields
    ) -> NDR:  # noqa: ANN003
        ndr = await self.get_ndr(ndr_id)
        clean = {k: v for k, v in fields.items() if v is not None}
        previous_status = ndr.status
        if clean:
            await self.ndrs.update(ndr, **clean)
            if "status" in clean:
                shipment = await self.shipments.get_by_id(ndr.shipment_id)
                if shipment is not None:
                    await self.shipments.update(shipment, ndr_status=clean["status"])
                await self.audit.record(
                    user=actor,
                    action="ndr.status_changed",
                    entity_type="ndr",
                    entity_id=str(ndr.id),
                    previous_value={"status": previous_status.value},
                    new_value={"status": clean["status"].value},
                )
        await self.session.commit()
        return ndr

    async def upsert_synced_ndr(self, **data) -> tuple[NDR, bool]:  # noqa: ANN003
        """Idempotent create-or-update from a sync adapter's normalized NDR
        dict. NDR/RTO have no independent identity in Shiprocket's data —
        both are always reported against an AWB, so the owning `Shipment`
        (and its `order_id`/`courier_id`) is resolved by AWB rather than
        invented (spec §16: "Do not invent NDR data").

        A shipment not yet found by AWB is not necessarily wrong data —
        the real Shiprocket sequence is Shopify order -> shipment created
        -> shipment synced -> NDR generated -> NDR synced, and each step
        can land in a different sync cycle (see `entity_sync._upsert_shipment`'s
        docstring for the shipment side of this same tolerance). Marking
        this specific `NotFoundError` with `error_type="dependency_not_ready"`
        (rather than the default, permanently-non-retryable `"not_found"`)
        is what makes `app.services.sync_service.SyncService._run_entity_sync`
        classify it as transient, so `app.tasks.retry_processing`'s scheduled
        retry naturally re-attempts this exact NDR later via the existing
        retry/backoff infrastructure — once the shipment has likely synced
        — instead of it being silently, permanently stranded. Never
        fabricates a `Shipment`/`Order` to work around a genuine gap.
        """
        source_system = data.pop("source_system")
        external_id = data.pop("external_id")
        awb = data.pop("awb", None)
        shiprocket_order_id = data.pop("shiprocket_order_id", None)
        data.pop("courier_name", None)

        shipment = await self.shipments.get_by_awb(awb) if awb else None
        if shipment is None:
            logger.warning(
                "shiprocket_ndr_shipment_resolution",
                shiprocket_ndr_id=external_id,
                awb=awb,
                shiprocket_order_id=shiprocket_order_id,
                matched=False,
                reason="no_oms_shipment_for_awb",
            )
            raise NotFoundError(
                f"No OMS shipment found for Shiprocket NDR (awb={awb!r}).",
                details={"error_type": "dependency_not_ready"},
            )

        clean = {k: v for k, v in data.items() if v is not None}
        ndr, created = await self.ndrs.upsert_by_external_id(
            source_system=source_system,
            external_id=external_id,
            shipment_id=shipment.id,
            order_id=shipment.order_id,
            courier_id=shipment.courier_id,
            **clean,
        )
        await self.shipments.update(shipment, ndr_status=ndr.status)
        await self.session.commit()
        return ndr, created

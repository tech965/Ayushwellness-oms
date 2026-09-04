from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.models.auth import User
from app.models.rto import RTO
from app.repositories.rto import RTORepository
from app.repositories.shipment import ShipmentRepository
from app.schemas.common import PageParams, SortParams
from app.services.audit_service import AuditService


class RTOService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.rtos = RTORepository(session)
        self.shipments = ShipmentRepository(session)
        self.audit = AuditService(session)

    async def list_rtos(
        self,
        *,
        page_params: PageParams,
        sort_params: SortParams,
        q: str | None = None,
        status: str | None = None,
        payment_type: str | None = None,
        courier_id: uuid.UUID | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
    ) -> tuple[list[RTO], int]:
        query = self.rtos.search_query(
            q=q,
            status=status,
            payment_type=payment_type,
            courier_id=courier_id,
            date_from=date_from,
            date_to=date_to,
        )
        items, total = await self.rtos.list(
            page_params=page_params, sort_params=sort_params, query=query
        )
        return list(items), total

    async def get_rto(self, rto_id: uuid.UUID) -> RTO:
        rto = await self.rtos.get_by_id(rto_id)
        if rto is None:
            raise NotFoundError("RTO not found.")
        return rto

    async def update_rto(
        self, rto_id: uuid.UUID, *, actor: User | None, **fields
    ) -> RTO:  # noqa: ANN003
        rto = await self.get_rto(rto_id)
        clean = {k: v for k, v in fields.items() if v is not None}
        previous_status = rto.status
        if clean:
            await self.rtos.update(rto, **clean)
            if "status" in clean:
                shipment = await self.shipments.get_by_id(rto.shipment_id)
                if shipment is not None:
                    await self.shipments.update(shipment, rto_status=clean["status"])
                await self.audit.record(
                    user=actor,
                    action="rto.status_changed",
                    entity_type="rto",
                    entity_id=str(rto.id),
                    previous_value={"status": previous_status.value},
                    new_value={"status": clean["status"].value},
                )
        await self.session.commit()
        return rto

    async def upsert_synced_rto(self, **data) -> tuple[RTO, bool]:  # noqa: ANN003
        """Idempotent create-or-update. Unlike NDR (a genuine Shiprocket
        list endpoint), no dedicated RTO listing endpoint could be
        confirmed without a live account (see docs/integrations/shiprocket.md)
        — RTO records are instead derived as a side effect of tracking
        refresh when a tracking event's mapped status is
        `RTO_INITIATED`/`RTO_DELIVERED` (`app.integrations.shiprocket.sync`),
        which already resolves `shipment_id`/`order_id`/`courier_id`.
        """
        source_system = data.pop("source_system")
        external_id = data.pop("external_id")
        shipment_id = data.pop("shipment_id")
        order_id = data.pop("order_id")
        courier_id = data.pop("courier_id", None)

        clean = {k: v for k, v in data.items() if v is not None}
        rto, created = await self.rtos.upsert_by_external_id(
            source_system=source_system,
            external_id=external_id,
            shipment_id=shipment_id,
            order_id=order_id,
            courier_id=courier_id,
            **clean,
        )
        shipment = await self.shipments.get_by_id(shipment_id)
        if shipment is not None:
            await self.shipments.update(shipment, rto_status=rto.status)
        await self.session.commit()
        return rto, created

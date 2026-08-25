"""Phase 1 dashboard: plain counts read straight from the database. No
delay-detection/intelligence logic here — that's Phase 3/4 (spec §41).
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.customer import Customer
from app.models.enums import NDRStatus, RTOStatus, ShipmentDelayStatus, ShipmentStatus
from app.models.ndr import NDR
from app.models.order import Order
from app.models.product import Product
from app.models.rto import RTO
from app.models.shipment import Shipment
from app.schemas.dashboard import DashboardSummaryResponse


class DashboardService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def _count(self, *entities, **filters) -> int:  # noqa: ANN002,ANN003
        stmt = select(func.count()).select_from(*entities)
        for condition in filters.values():
            stmt = stmt.where(condition)
        return (await self.session.execute(stmt)).scalar() or 0

    async def get_summary(self) -> DashboardSummaryResponse:
        total_orders = await self._count(Order)
        total_customers = await self._count(Customer)
        total_products = await self._count(Product)
        total_shipments = await self._count(Shipment)
        delivered_shipments = await self._count(
            Shipment, delivered=(Shipment.current_status == ShipmentStatus.DELIVERED)
        )
        delayed_shipments = await self._count(
            Shipment, delayed=(Shipment.delay_status == ShipmentDelayStatus.DELAYED)
        )
        open_ndr_count = await self._count(NDR, open_=(NDR.status == NDRStatus.OPEN))
        open_rto_count = await self._count(
            RTO, open_=(RTO.status.in_([RTOStatus.INITIATED, RTOStatus.IN_TRANSIT]))
        )

        return DashboardSummaryResponse(
            total_orders=total_orders,
            total_customers=total_customers,
            total_products=total_products,
            total_shipments=total_shipments,
            delivered_shipments=delivered_shipments,
            delayed_shipments=delayed_shipments,
            open_ndr_count=open_ndr_count,
            open_rto_count=open_rto_count,
        )

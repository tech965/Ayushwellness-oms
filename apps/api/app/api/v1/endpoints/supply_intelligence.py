"""India Supply Intelligence — state-level demand/logistics analytics.
See `app.services.supply_intelligence_service` for the aggregation logic
and data-source rationale.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.dependencies.auth import require_permission
from app.models.auth import User
from app.schemas.response import ApiResponse
from app.schemas.supply_intelligence import SupplyIntelligenceResponse
from app.services.supply_intelligence_service import SupplyIntelligenceService

router = APIRouter()


@router.get("", response_model=ApiResponse[SupplyIntelligenceResponse])
async def get_supply_intelligence(
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
    state: str | None = Query(default=None, description="Canonical Indian state/UT name"),
    session: AsyncSession = Depends(get_db),
    # Reuses the existing dashboard analytics permission -- this is the
    # same class of read-only business-intelligence data (order/shipment
    # aggregates), not a distinct capability that warrants its own
    # RBAC row.
    _: User = Depends(require_permission("analytics.read")),
) -> ApiResponse[SupplyIntelligenceResponse]:
    data = await SupplyIntelligenceService(session).get_supply_intelligence(
        date_from, date_to, state
    )
    return ApiResponse(data=data)

"""Operations Command Center. See
`app.services.operations_command_center_service` for how this
orchestrates the existing analytics/supply-intelligence services rather
than re-deriving their numbers.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.dependencies.auth import require_permission
from app.models.auth import User
from app.schemas.operations_command_center import OperationsCommandCenterResponse
from app.schemas.response import ApiResponse
from app.services.operations_command_center_service import OperationsCommandCenterService

router = APIRouter()


@router.get("", response_model=ApiResponse[OperationsCommandCenterResponse])
async def get_operations_command_center(
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    # Same permission the dashboard/analytics and India Supply
    # Intelligence endpoints already use -- this is the same class of
    # read-only business-intelligence data, not a distinct capability.
    _: User = Depends(require_permission("analytics.read")),
) -> ApiResponse[OperationsCommandCenterResponse]:
    data = await OperationsCommandCenterService(session).get_command_center(date_from, date_to)
    return ApiResponse(data=data)

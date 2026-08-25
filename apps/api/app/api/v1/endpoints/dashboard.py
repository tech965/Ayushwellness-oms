"""Phase 1 dashboard: plain counts, no intelligence — see
`app.services.dashboard_service` docstring.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.dependencies.auth import require_permission
from app.models.auth import User
from app.schemas.dashboard import DashboardSummaryResponse
from app.schemas.response import ApiResponse
from app.services.dashboard_service import DashboardService

router = APIRouter()


@router.get("/summary", response_model=ApiResponse[DashboardSummaryResponse])
async def get_dashboard_summary(
    session: AsyncSession = Depends(get_db),
    _: User = Depends(require_permission("analytics.read")),
) -> ApiResponse[DashboardSummaryResponse]:
    summary = await DashboardService(session).get_summary()
    return ApiResponse(data=summary)

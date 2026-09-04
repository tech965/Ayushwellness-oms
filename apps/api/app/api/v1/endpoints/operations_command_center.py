"""Operations Command Center. See
`app.services.operations_command_center_service` for how this
orchestrates the existing analytics/supply-intelligence services rather
than re-deriving their numbers.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BeforeValidator
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.dependencies.auth import require_permission
from app.models.auth import User
from app.schemas.operations_command_center import OperationsCommandCenterResponse
from app.schemas.response import ApiResponse
from app.services.operations_command_center_service import OperationsCommandCenterService

router = APIRouter()

# `?date_from=&date_to=` (present but empty -- e.g. a client that always
# includes both query keys rather than omitting unset ones) previously
# returned 422 here: Pydantic's datetime coercion rejects an empty string
# outright ("Input should be a valid datetime or date, input is too
# short") -- confirmed live against the real endpoint. `date_from`/
# `date_to` are documented as optional; an empty string is a client's way
# of saying "not set", so it's normalized to `None` before Pydantic's
# datetime parsing runs, same as an omitted param. A genuinely malformed
# non-empty value (e.g. "not-a-date") still fails validation exactly as
# before -- only the empty-string case changes.
#
# Must use a plain `= None` default, not `= Query(default=None)`: with
# `Query(...)` as the default, FastAPI never invokes this `BeforeValidator`
# at all (confirmed empirically) -- silently leaving the empty-string
# case unfixed while looking correct.
_OptionalDateTimeQuery = Annotated[datetime | None, BeforeValidator(lambda v: v or None)]


@router.get("", response_model=ApiResponse[OperationsCommandCenterResponse])
async def get_operations_command_center(
    date_from: _OptionalDateTimeQuery = None,
    date_to: _OptionalDateTimeQuery = None,
    session: AsyncSession = Depends(get_db),
    # Same permission the dashboard/analytics and India Supply
    # Intelligence endpoints already use -- this is the same class of
    # read-only business-intelligence data, not a distinct capability.
    _: User = Depends(require_permission("analytics.read")),
) -> ApiResponse[OperationsCommandCenterResponse]:
    data = await OperationsCommandCenterService(session).get_command_center(date_from, date_to)
    return ApiResponse(data=data)

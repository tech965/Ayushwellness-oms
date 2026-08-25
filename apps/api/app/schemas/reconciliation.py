from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class ReconciliationRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    triggered_by_user_id: uuid.UUID | None
    status: str
    started_at: datetime | None
    completed_at: datetime | None
    total_checked: int
    reconciled_count: int
    mismatch_count: int
    missing_count: int
    error_count: int
    run_metadata: dict[str, Any] | None
    created_at: datetime


class ReconciliationResultResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    run_id: uuid.UUID
    check_type: str
    provider: str
    entity_type: str
    internal_id: str | None
    external_id: str | None
    expected_value: dict[str, Any] | None
    actual_value: dict[str, Any] | None
    status: str
    message: str | None
    resolved: bool
    resolved_at: datetime | None
    resolved_by_user_id: uuid.UUID | None
    created_at: datetime

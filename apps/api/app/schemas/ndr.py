from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.enums import NDRStatus


class NDRUpdateRequest(BaseModel):
    status: NDRStatus | None = None
    customer_response: str | None = None
    reattempt_status: str | None = None
    reattempt_date: datetime | None = None
    notes: str | None = None


class NDRResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    shipment_id: uuid.UUID
    order_id: uuid.UUID
    courier_id: uuid.UUID | None
    reason: str | None
    normalized_reason: str | None
    external_reason: str | None
    attempt_number: int
    status: NDRStatus
    customer_response: str | None
    reattempt_status: str | None
    reattempt_date: datetime | None
    notes: str | None
    source_system: str | None
    created_at: datetime
    updated_at: datetime

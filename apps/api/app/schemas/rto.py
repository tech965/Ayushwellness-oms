from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.enums import RTOStatus


class RTOUpdateRequest(BaseModel):
    status: RTOStatus | None = None
    notes: str | None = None


class RTOResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    shipment_id: uuid.UUID
    order_id: uuid.UUID
    courier_id: uuid.UUID | None
    reason: str | None
    normalized_reason: str | None
    external_reason: str | None
    status: RTOStatus
    initiated_at: datetime | None
    completed_at: datetime | None
    notes: str | None
    source_system: str | None
    created_at: datetime
    updated_at: datetime

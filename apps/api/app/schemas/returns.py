from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import ReturnStatus


class ReturnCreateRequest(BaseModel):
    order_id: uuid.UUID
    order_item_id: uuid.UUID | None = None
    customer_id: uuid.UUID | None = None
    reason: str | None = None
    quantity: int = Field(default=1, gt=0)


class ReturnUpdateRequest(BaseModel):
    status: ReturnStatus | None = None
    notes: str | None = None


class ReturnResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    order_id: uuid.UUID
    order_item_id: uuid.UUID | None
    customer_id: uuid.UUID | None
    reason: str | None
    status: ReturnStatus
    quantity: int
    requested_at: datetime | None
    approved_at: datetime | None
    received_at: datetime | None
    completed_at: datetime | None
    notes: str | None
    source_system: str | None
    created_at: datetime
    updated_at: datetime

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import NDRStatus, RTOStatus, ShipmentDelayStatus, ShipmentStatus


class ShipmentCreateRequest(BaseModel):
    order_id: uuid.UUID
    awb: str | None = None
    courier_id: uuid.UUID | None = None
    expected_delivery_date: datetime | None = None


class ShipmentUpdateRequest(BaseModel):
    courier_id: uuid.UUID | None = None
    current_status: ShipmentStatus | None = None
    expected_delivery_date: datetime | None = None
    current_location: str | None = None


class ShipmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    order_id: uuid.UUID
    shiprocket_shipment_id: str | None
    awb: str | None
    courier_id: uuid.UUID | None
    current_status: ShipmentStatus
    delay_status: ShipmentDelayStatus
    ndr_status: NDRStatus | None
    rto_status: RTOStatus | None
    pickup_date: datetime | None
    expected_delivery_date: datetime | None
    actual_delivery_date: datetime | None
    current_location: str | None
    last_tracking_update_at: datetime | None
    source_system: str | None
    created_at: datetime
    updated_at: datetime


class ShipmentEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    shipment_id: uuid.UUID
    external_event_id: str | None
    status: str
    location: str | None
    event_timestamp: datetime
    description: str | None
    courier_name: str | None
    source: str
    created_at: datetime


class ShipmentEventCreateRequest(BaseModel):
    external_event_id: str | None = None
    status: str = Field(min_length=1, max_length=100)
    location: str | None = None
    event_timestamp: datetime
    description: str | None = None
    courier_name: str | None = None

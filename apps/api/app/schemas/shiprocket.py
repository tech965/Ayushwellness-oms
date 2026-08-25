"""Request bodies for the Shiprocket operational actions (spec §26) —
push operations from the OMS to Shiprocket, distinct from the pull-sync
schemas in `app.schemas.integration`.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ShiprocketShipRequest(BaseModel):
    """Optional package-dimension overrides for `POST /orders/{id}/ship`
    — the OMS has no per-order package-dimension data yet (see
    docs/roadmap.md), so these default to a small-parcel placeholder.
    """

    length_cm: float = Field(default=10.0, gt=0)
    breadth_cm: float = Field(default=10.0, gt=0)
    height_cm: float = Field(default=10.0, gt=0)
    weight_kg: float = Field(default=0.5, gt=0)


class ShiprocketAssignAwbRequest(BaseModel):
    courier_id: str | None = None


class ShiprocketNdrReattemptRequest(BaseModel):
    address_1: str = Field(min_length=1, max_length=255)
    address_2: str | None = None
    phone: str = Field(min_length=1, max_length=32)

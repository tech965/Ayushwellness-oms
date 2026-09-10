"""Request bodies for the Shiprocket operational actions (spec §26) —
push operations from the OMS to Shiprocket, distinct from the pull-sync
schemas in `app.schemas.integration`.
"""

from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, Field


class LocateShiprocketOrderRequest(BaseModel):
    """One or many OMS order ids to resolve to their existing Shiprocket
    order id -- never creates anything, see
    `app.services.shiprocket_service.locate_shiprocket_orders`.
    """

    order_ids: list[uuid.UUID] = Field(min_length=1, max_length=100)


class LocateShiprocketOrderResult(BaseModel):
    order_id: uuid.UUID
    status: Literal["found", "not_found", "error"]
    # The real, numeric Shiprocket order id -- NOT a URL. Shiprocket has
    # no supported deep-link filter for its "Ready to Ship" page, so the
    # frontend opens that page plain and copies this id to the clipboard
    # for the operator to paste into Shiprocket's own "Multiple Order
    # IDs" filter (see `app.services.shiprocket_service.
    # SHIPROCKET_READY_TO_SHIP_URL`'s docstring).
    shiprocket_order_id: str | None = None
    message: str | None = None


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

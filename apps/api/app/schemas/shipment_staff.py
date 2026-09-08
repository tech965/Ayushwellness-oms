"""Admin-only oversight of Shipment Staff — see
`ShipmentStaffService.list_shipment_staff_performance`. Everything a
Shipment Staff user sees of their own scope reuses the existing
`ShipmentQueueRowResponse`/`ShipmentResponse`/`ShipmentSummaryResponse`/
`ShipmentAnalyticsResponse`/`NDRListResponse`/`RTOListResponse` schemas
unchanged — this file only adds the one response shape that's genuinely
new (per-Shipment-Staff-user aggregated performance).
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel


class ShipmentStaffPerformanceResponse(BaseModel):
    shipment_staff_id: uuid.UUID
    shipment_staff_name: str
    telecaller_count: int
    confirmed: int
    shipped: int
    delivered: int
    ndr: int
    rto: int

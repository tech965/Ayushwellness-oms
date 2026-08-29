"""Response shapes for `app/api/v1/endpoints/analytics.py` — the
dashboard's business-intelligence endpoints (fills in the Phase-3 stub
`analytics.py` was left as in Phase 1)."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel


class KPIValue(BaseModel):
    current: Decimal
    previous: Decimal
    # None when `previous` is 0 — a percentage change against zero is
    # undefined, not infinite or 100%; the frontend renders that as "—"/"new".
    change_pct: float | None


class AnalyticsSummaryResponse(BaseModel):
    date_from: datetime
    date_to: datetime
    total_orders: KPIValue
    total_revenue: KPIValue
    total_customers: KPIValue
    total_products: KPIValue
    fulfilled_orders: KPIValue
    unfulfilled_orders: KPIValue
    cod_orders: KPIValue
    prepaid_orders: KPIValue
    pending_orders: KPIValue
    cod_value: KPIValue
    prepaid_value: KPIValue
    delivered_shipments: KPIValue
    in_transit_shipments: KPIValue
    out_for_delivery_shipments: KPIValue
    delayed_shipments: KPIValue
    open_ndr: KPIValue
    open_rto: KPIValue
    returns: KPIValue
    refunds: KPIValue


class TimeseriesPoint(BaseModel):
    bucket: str
    order_count: int
    revenue: Decimal


class OrdersTimeseriesResponse(BaseModel):
    interval: str
    points: list[TimeseriesPoint]


class StatusCount(BaseModel):
    status: str
    count: int


class BreakdownsResponse(BaseModel):
    order_status: list[StatusCount]
    payment_type: list[StatusCount]
    payment_status: list[StatusCount]
    fulfillment_status: list[StatusCount]
    shipment_status: list[StatusCount]


class TopProduct(BaseModel):
    sku: str
    title: str
    units_sold: int
    revenue: Decimal


class CourierPerformance(BaseModel):
    courier_id: uuid.UUID
    name: str
    shipment_count: int
    delivered_count: int
    ndr_count: int
    rto_count: int
    delivered_pct: float
    ndr_pct: float
    rto_pct: float


class RecentOrder(BaseModel):
    id: uuid.UUID
    order_number: str
    total_amount: Decimal
    status: str
    created_at: datetime


class RecentShipment(BaseModel):
    id: uuid.UUID
    order_id: uuid.UUID
    awb: str | None
    current_status: str
    updated_at: datetime


class RecentNdrRto(BaseModel):
    id: uuid.UUID
    order_id: uuid.UUID
    kind: str
    status: str
    reason: str | None
    created_at: datetime


class RecentPayment(BaseModel):
    id: uuid.UUID
    order_id: uuid.UUID
    amount: Decimal
    status: str
    created_at: datetime


class RecentActivityResponse(BaseModel):
    recent_orders: list[RecentOrder]
    recent_shipments: list[RecentShipment]
    recent_ndr_rto: list[RecentNdrRto]
    recent_payments: list[RecentPayment]

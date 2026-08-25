from __future__ import annotations

from pydantic import BaseModel


class DashboardSummaryResponse(BaseModel):
    total_orders: int
    total_customers: int
    total_products: int
    total_shipments: int
    delivered_shipments: int
    delayed_shipments: int
    open_ndr_count: int
    open_rto_count: int

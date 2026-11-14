"""Response shapes for `app/api/v1/endpoints/marketplace.py` — the
Marketplace Orders module's dashboard summary. Reuses `KPIValue`/the same
current-vs-previous-period shape every other OMS dashboard card already
uses (`app.schemas.analytics.KPIValue`), never a second comparison format.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from app.schemas.analytics import KPIValue


class MarketplaceDashboardSummaryResponse(BaseModel):
    date_from: datetime
    date_to: datetime
    total_orders: KPIValue
    amazon_orders: KPIValue
    flipkart_orders: KPIValue
    meesho_orders: KPIValue
    total_sales: KPIValue
    pending_orders: KPIValue
    unfulfilled_orders: KPIValue
    shipped_orders: KPIValue
    delivered_orders: KPIValue
    cancelled_orders: KPIValue
    cod_orders: KPIValue
    prepaid_orders: KPIValue

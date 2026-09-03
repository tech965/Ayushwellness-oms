"""Response shapes for `app/api/v1/endpoints/supply_intelligence.py` — the
India Supply Intelligence page (state-level demand/logistics analytics).

Deliberately a standalone schema module, not an addition to
`app/schemas/analytics.py` — this feature reads the same underlying
tables as the dashboard's analytics but is conceptually a separate
report, and keeping it isolated means nothing here can accidentally
change an existing analytics response shape.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel

OpportunityBucket = Literal["scale", "opportunity", "investigate", "untapped", "steady"]


class StateMetric(BaseModel):
    state: str
    orders: int
    revenue: Decimal
    customers: int
    delivered: int
    in_transit: int
    pending: int
    rto: int
    ndr: int
    # None when the state has no shipments yet -- a rate against zero
    # shipments is undefined, never a fabricated 0%.
    rto_rate_pct: float | None
    # None when the previous comparable period had zero orders for this
    # state -- percentage growth against zero is undefined, not infinite.
    growth_pct: float | None
    opportunity: OpportunityBucket


class CityMetric(BaseModel):
    city: str
    orders: int
    revenue: Decimal


class StateProductMetric(BaseModel):
    sku: str
    product_name: str
    orders: int
    quantity: int
    revenue: Decimal
    avg_order_value: Decimal


class StateDetail(BaseModel):
    state: str
    orders: int
    revenue: Decimal
    avg_order_value: Decimal
    customers: int
    delivered: int
    in_transit: int
    pending: int
    rto: int
    ndr: int
    rto_rate_pct: float | None
    cities: list[CityMetric]
    products: list[StateProductMetric]


class SupplyIntelligenceSummary(BaseModel):
    total_orders: int
    total_revenue: Decimal
    active_states: int
    top_state: str | None
    top_revenue_state: str | None


class MarketInsight(BaseModel):
    type: Literal[
        "strongest_market",
        "fastest_growing",
        "emerging_market",
        "attention_required",
        "untapped_markets",
    ]
    title: str
    description: str
    # The state(s) the insight is about, when it names one -- empty for
    # "not enough data" insights and for "untapped_markets" (which names
    # several states in `description` instead of picking one).
    states: list[str]


class SupplyIntelligencePeriod(BaseModel):
    date_from: datetime
    date_to: datetime


class SupplyIntelligenceResponse(BaseModel):
    summary: SupplyIntelligenceSummary
    states: list[StateMetric]
    selected_state: StateDetail | None
    insights: list[MarketInsight]
    period: SupplyIntelligencePeriod
    comparison_period: SupplyIntelligencePeriod
    # Orders in range with no shipping address, or a state value that
    # doesn't match any known Indian state/UT (typos, non-India
    # addresses, etc.) -- excluded from every state figure above rather
    # than guessed into one, and surfaced here so the UI can disclose it
    # honestly instead of silently under-counting.
    unmapped_order_count: int

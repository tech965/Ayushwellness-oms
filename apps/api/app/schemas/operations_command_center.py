"""Response shapes for
`app/api/v1/endpoints/operations_command_center.py` -- the Operations
Command Center page (an executive "what needs attention / how healthy
are we / where's the opportunity" rollup).

Deliberately a standalone schema module (like
`app/schemas/supply_intelligence.py`) -- this is a distinct report, not
a dashboard KPI, and most of its numbers are computed by ORCHESTRATING
`AnalyticsService`/`SupplyIntelligenceService` (see the service module's
docstring), not by re-deriving them here.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel

Priority = Literal["critical", "warning", "opportunity", "positive"]


class CommandCenterSummary(BaseModel):
    total_orders: int
    # None when the previous comparable period had zero orders --
    # percentage growth against zero is undefined, never fabricated.
    orders_growth_pct: float | None
    total_revenue: Decimal
    requires_attention_count: int


class AttentionItem(BaseModel):
    type: str
    label: str
    count: int
    # None when a monetary figure isn't meaningful for this item (e.g.
    # NDR count has no associated amount).
    amount: Decimal | None
    priority: Priority
    href: str


class MetricPair(BaseModel):
    """A two-sided health metric (e.g. delivered vs. pending). Either
    side is `None` -- not 0 -- when the underlying data genuinely
    couldn't be computed for this period, so the UI can render "—"
    instead of implying "zero" (spec section 6/14).
    """

    label: str
    value: int | None


class OperationsHealth(BaseModel):
    orders: list[MetricPair]
    payments: list[MetricPair]
    shipments: list[MetricPair]
    returns: list[MetricPair]
    refunds: list[MetricPair]


class BusinessOpportunity(BaseModel):
    type: str
    title: str
    description: str


class CommandCenterInsight(BaseModel):
    priority: Priority
    message: str


class CommandCenterPeriod(BaseModel):
    date_from: datetime
    date_to: datetime


class OperationsCommandCenterResponse(BaseModel):
    summary: CommandCenterSummary
    attention_items: list[AttentionItem]
    operations_health: OperationsHealth
    business_opportunities: list[BusinessOpportunity]
    insights: list[CommandCenterInsight]
    period: CommandCenterPeriod
    comparison_period: CommandCenterPeriod

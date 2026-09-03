"""Operations Command Center — an executive rollup of "what needs
attention / how healthy is the business / where's the opportunity,"
built almost entirely by ORCHESTRATING existing services rather than
re-deriving their numbers:

  - `AnalyticsService.get_summary` -- total orders/revenue, fulfilled/
    unfulfilled, COD/prepaid, shipment status counts, open NDR/RTO,
    returns/refunds counts, every one already current-vs-previous-period
    scored (`KPIValue.change_pct`).
  - `AnalyticsService.get_payment_status_breakdown` -- paid vs. pending
    payment counts/revenue.
  - `AnalyticsService.get_returns_refunds_summary` -- pending/completed
    returns and refunds (added for the dashboard's Returns/Refunds card).
  - `AnalyticsService.get_top_products` -- called twice (current period,
    previous period) to find the fastest-growing/highest-volume product,
    the same way this page needs a "previous period" product comparison
    that no single existing call already returns.
  - `SupplyIntelligenceService.get_supply_intelligence` -- top state,
    top revenue state, fastest-growing state, emerging market (the
    India Supply Intelligence page's own insight engine).

Only two numbers here have no existing source and get a small, isolated
query each (`_cod_pending_fulfillment_count`, `_pending_shipment_count`)
-- both single grouped COUNTs, not a new analytics subsystem.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import (
    FulfillmentStatus,
    PaymentType,
    ShipmentStatus,
)
from app.models.order import Order
from app.models.shipment import Shipment
from app.schemas.analytics import AnalyticsSummaryResponse, TopProduct
from app.schemas.operations_command_center import (
    AttentionItem,
    BusinessOpportunity,
    CommandCenterInsight,
    CommandCenterPeriod,
    CommandCenterSummary,
    MetricPair,
    OperationsCommandCenterResponse,
    OperationsHealth,
)
from app.schemas.supply_intelligence import SupplyIntelligenceResponse
from app.services.analytics_service import AnalyticsService, DateRange, resolve_range
from app.services.supply_intelligence_service import SupplyIntelligenceService

# Thresholds behind insight priority (spec section 9) -- fixed,
# documented percentages, not per-dataset statistics, so a demo can
# reproduce and explain every priority assignment.
_HIGH_RTO_DELTA_PCT = 5.0  # a state's RTO rate this many points above the overall average
_LARGE_BACKLOG_ORDER_CEILING = 20  # unfulfilled/pending count considered "critical" on its own


class OperationsCommandCenterService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.analytics = AnalyticsService(session)
        self.supply_intelligence = SupplyIntelligenceService(session)

    async def get_command_center(
        self, date_from: datetime | None, date_to: datetime | None
    ) -> OperationsCommandCenterResponse:
        current = resolve_range(date_from, date_to)

        summary_response = await self.analytics.get_summary(date_from, date_to)
        payment_breakdown = await self.analytics.get_payment_status_breakdown(
            date_from, date_to, None
        )
        returns_refunds = await self.analytics.get_returns_refunds_summary(date_from, date_to)
        supply = await self.supply_intelligence.get_supply_intelligence(date_from, date_to, None)

        cod_pending_fulfillment = await self._cod_pending_fulfillment_count(current)
        pending_shipments = await self._pending_shipment_count(current)

        current_products = await self.analytics.get_top_products(date_from, date_to, limit=10)
        previous = DateRange(
            date_from=current.date_from - (current.date_to - current.date_from),
            date_to=current.date_from,
        )
        previous_products = await self.analytics.get_top_products(
            previous.date_from, previous.date_to, limit=50
        )
        previous_units_by_sku = {p.sku: p.units_sold for p in previous_products}

        unfulfilled_count = int(summary_response.unfulfilled_orders.current)

        attention_items = _build_attention_items(
            unfulfilled_count=unfulfilled_count,
            cod_pending_fulfillment=cod_pending_fulfillment,
            pending_payment_count=payment_breakdown.pending_count,
            pending_payment_amount=payment_breakdown.pending_revenue,
            pending_shipments=pending_shipments,
            open_ndr=int(summary_response.open_ndr.current),
            open_rto=int(summary_response.open_rto.current),
            pending_returns=returns_refunds.returns.pending_returns,
            pending_refunds=returns_refunds.refunds.pending_refunds,
        )
        requires_attention_count = sum(item.count for item in attention_items)

        operations_health = OperationsHealth(
            orders=[
                MetricPair(label="Fulfilled", value=int(summary_response.fulfilled_orders.current)),
                MetricPair(
                    label="Unfulfilled", value=int(summary_response.unfulfilled_orders.current)
                ),
            ],
            payments=[
                MetricPair(label="Paid", value=payment_breakdown.paid_count),
                MetricPair(label="Pending", value=payment_breakdown.pending_count),
            ],
            shipments=[
                MetricPair(
                    label="Delivered", value=int(summary_response.delivered_shipments.current)
                ),
                MetricPair(
                    label="In Transit", value=int(summary_response.in_transit_shipments.current)
                ),
                MetricPair(label="Pending", value=pending_shipments),
                MetricPair(label="NDR", value=int(summary_response.open_ndr.current)),
                MetricPair(label="RTO", value=int(summary_response.open_rto.current)),
            ],
            returns=[
                MetricPair(
                    label="Completed", value=returns_refunds.returns.completed_returns
                ),
                MetricPair(label="Pending", value=returns_refunds.returns.pending_returns),
            ],
            refunds=[
                MetricPair(
                    label="Completed", value=returns_refunds.refunds.completed_refunds
                ),
                MetricPair(label="Pending", value=returns_refunds.refunds.pending_refunds),
            ],
        )

        business_opportunities = _build_opportunities(supply)
        insights = _build_insights(
            summary_response=summary_response,
            cod_pending_fulfillment=cod_pending_fulfillment,
            pending_shipments=pending_shipments,
            supply=supply,
            current_products=current_products,
            previous_units_by_sku=previous_units_by_sku,
        )

        return OperationsCommandCenterResponse(
            summary=CommandCenterSummary(
                total_orders=int(summary_response.total_orders.current),
                orders_growth_pct=summary_response.total_orders.change_pct,
                total_revenue=summary_response.total_revenue.current,
                requires_attention_count=requires_attention_count,
            ),
            attention_items=attention_items,
            operations_health=operations_health,
            business_opportunities=business_opportunities,
            insights=insights,
            period=CommandCenterPeriod(date_from=current.date_from, date_to=current.date_to),
            comparison_period=CommandCenterPeriod(
                date_from=previous.date_from, date_to=previous.date_to
            ),
        )

    async def _cod_pending_fulfillment_count(self, r: DateRange) -> int:
        stmt = select(func.count()).select_from(Order).where(
            Order.order_datetime >= r.date_from,
            Order.order_datetime <= r.date_to,
            Order.payment_type == PaymentType.COD,
            Order.fulfillment_status == FulfillmentStatus.UNFULFILLED,
        )
        return int(await self.session.scalar(stmt) or 0)

    async def _pending_shipment_count(self, r: DateRange) -> int:
        # Scoped by `Shipment.updated_at`, matching the same convention
        # `AnalyticsService.get_summary`'s in_transit/out_for_delivery/
        # delayed shipment counts already use for a "current status
        # snapshot within this period" figure.
        stmt = select(func.count()).select_from(Shipment).where(
            Shipment.updated_at >= r.date_from,
            Shipment.updated_at <= r.date_to,
            Shipment.current_status == ShipmentStatus.PENDING,
        )
        return int(await self.session.scalar(stmt) or 0)


def _build_attention_items(
    *,
    unfulfilled_count: int,
    cod_pending_fulfillment: int,
    pending_payment_count: int,
    pending_payment_amount: Decimal,
    pending_shipments: int,
    open_ndr: int,
    open_rto: int,
    pending_returns: int,
    pending_refunds: int,
) -> list[AttentionItem]:
    def priority_for(count: int) -> str:
        return "critical" if count >= _LARGE_BACKLOG_ORDER_CEILING else "warning"

    return [
        AttentionItem(
            type="unfulfilled_orders",
            label="Unfulfilled Orders",
            count=unfulfilled_count,
            amount=None,
            priority=priority_for(unfulfilled_count),
            href="/orders?fulfillment_status=unfulfilled",
        ),
        AttentionItem(
            type="pending_payments",
            label="Pending Payments",
            count=pending_payment_count,
            amount=pending_payment_amount,
            priority=priority_for(pending_payment_count),
            href="/orders?payment_status=pending",
        ),
        AttentionItem(
            type="shipment_pending",
            label="Shipment Pending",
            count=pending_shipments,
            amount=None,
            priority=priority_for(pending_shipments),
            href="/orders?shipment_status=pending",
        ),
        AttentionItem(
            type="ndr_risk",
            label="NDR Risk",
            count=open_ndr,
            amount=None,
            priority=priority_for(open_ndr),
            href="/ndr",
        ),
        AttentionItem(
            type="rto_risk",
            label="RTO Risk",
            count=open_rto,
            amount=None,
            priority=priority_for(open_rto),
            href="/rto",
        ),
        AttentionItem(
            type="pending_returns",
            label="Pending Returns",
            count=pending_returns,
            amount=None,
            priority=priority_for(pending_returns),
            href="/returns",
        ),
        AttentionItem(
            type="pending_refunds",
            label="Pending Refunds",
            count=pending_refunds,
            amount=None,
            priority=priority_for(pending_refunds),
            href="/refunds",
        ),
        AttentionItem(
            type="cod_pending_fulfillment",
            label="COD Orders Pending Fulfillment",
            count=cod_pending_fulfillment,
            amount=None,
            priority=priority_for(cod_pending_fulfillment),
            href="/orders?payment_type=cod&fulfillment_status=unfulfilled",
        ),
    ]


def _build_opportunities(supply: SupplyIntelligenceResponse) -> list[BusinessOpportunity]:
    """Reuses `SupplyIntelligenceService`'s own already-computed summary/
    insights -- never re-derives state-level growth/revenue here.
    """
    opportunities: list[BusinessOpportunity] = []

    if supply.summary.top_revenue_state:
        opportunities.append(
            BusinessOpportunity(
                type="top_revenue_state",
                title="Top Revenue State",
                description=f"{supply.summary.top_revenue_state} generated the highest revenue "
                "in this period.",
            )
        )
    else:
        opportunities.append(
            BusinessOpportunity(
                type="top_revenue_state",
                title="Top Revenue State",
                description="Not enough data yet",
            )
        )

    fastest_growing = next(
        (i for i in supply.insights if i.type == "fastest_growing" and i.states), None
    )
    opportunities.append(
        BusinessOpportunity(
            type="fastest_growing_state",
            title="Fast Growing State",
            description=fastest_growing.description if fastest_growing else "Not enough data yet",
        )
    )

    emerging = next((i for i in supply.insights if i.type == "emerging_market" and i.states), None)
    opportunities.append(
        BusinessOpportunity(
            type="emerging_market",
            title="Emerging Market",
            description=emerging.description if emerging else "Not enough data yet",
        )
    )

    return opportunities


def _build_insights(
    *,
    summary_response: AnalyticsSummaryResponse,
    cod_pending_fulfillment: int,
    pending_shipments: int,
    supply: SupplyIntelligenceResponse,
    current_products: list[TopProduct],
    previous_units_by_sku: dict[str, int],
) -> list[CommandCenterInsight]:
    insights: list[CommandCenterInsight] = []

    if cod_pending_fulfillment > 0:
        insights.append(
            CommandCenterInsight(
                priority="critical" if cod_pending_fulfillment >= _LARGE_BACKLOG_ORDER_CEILING
                else "warning",
                message=f"🔴 {cod_pending_fulfillment} COD order(s) are currently pending "
                "fulfillment.",
            )
        )

    attention_states = next(
        (i for i in supply.insights if i.type == "attention_required" and i.states), None
    )
    if attention_states:
        overall_rates = [s.rto_rate_pct for s in supply.states if s.rto_rate_pct is not None]
        overall_avg = sum(overall_rates) / len(overall_rates) if overall_rates else None
        worst_state = max(
            (s for s in supply.states if s.state in attention_states.states),
            key=lambda s: s.rto_rate_pct or 0,
        )
        if overall_avg is not None and worst_state.rto_rate_pct is not None:
            delta = worst_state.rto_rate_pct - overall_avg
            if delta >= _HIGH_RTO_DELTA_PCT:
                insights.append(
                    CommandCenterInsight(
                        priority="warning",
                        message=(
                            f"🟠 {worst_state.state} has an elevated RTO rate "
                            f"({worst_state.rto_rate_pct:.1f}% vs. {overall_avg:.1f}% "
                            "overall average)."
                        ),
                    )
                )

    if pending_shipments > 0:
        insights.append(
            CommandCenterInsight(
                priority="warning",
                message=f"🟡 {pending_shipments} shipment(s) are currently pending processing.",
            )
        )

    orders_growth = summary_response.total_orders.change_pct
    if orders_growth is not None:
        if orders_growth > 0:
            insights.append(
                CommandCenterInsight(
                    priority="positive",
                    message=f"🟢 Orders increased {orders_growth:.1f}% compared with the "
                    "previous period.",
                )
            )
        elif orders_growth < 0:
            insights.append(
                CommandCenterInsight(
                    priority="warning",
                    message=f"🟠 Orders decreased {abs(orders_growth):.1f}% compared with the "
                    "previous period.",
                )
            )

    if supply.summary.top_revenue_state:
        insights.append(
            CommandCenterInsight(
                priority="positive",
                message=f"💡 {supply.summary.top_revenue_state} generated the highest revenue "
                "during this period.",
            )
        )

    growing_products = [
        p
        for p in current_products
        if p.sku in previous_units_by_sku and previous_units_by_sku[p.sku] > 0
    ]
    if growing_products:
        best = max(
            growing_products,
            key=lambda p: (p.units_sold - previous_units_by_sku[p.sku])
            / previous_units_by_sku[p.sku],
        )
        growth = (
            (best.units_sold - previous_units_by_sku[best.sku])
            / previous_units_by_sku[best.sku]
            * 100
        )
        if growth > 0:
            insights.append(
                CommandCenterInsight(
                    priority="opportunity",
                    message=f"🟡 {best.title} demand grew {growth:.1f}% compared with the "
                    "previous period.",
                )
            )

    return insights

"""India Supply Intelligence — state-level demand/logistics analytics.

Reads `Order.shipping_address` (an existing JSON snapshot column, already
populated by the Shopify normalizer from `province`/`city` -- see
`app/integrations/shopify/normalizer.py:normalize_address`) and joins the
existing `Shipment`/`OrderItem` tables the same way the dashboard's own
`AnalyticsService.get_courier_performance`/`get_top_products` already do.
No new table, no new column -- this is a read-only aggregation layer over
data that already exists.

Deliberately a standalone service (not an addition to
`AnalyticsService`) -- this is a distinct report, not a dashboard KPI,
and keeping it isolated means nothing here can affect the dashboard's
existing, already-tested queries. A handful of small helpers are
duplicated rather than imported from `analytics_service.py` (date-range
resolution, delivered/in-transit/pending/RTO/NDR status groupings) --
that module's helpers are private (leading underscore) and this file
intentionally never reaches into another module's private internals;
the duplication is a few lines of trivial, stable logic, not the kind of
large shared computation the brief says not to duplicate.

State-name matching: `shipping_address['state']` is free text (whatever
Shopify's `province` field contained), so it's normalized against a
canonical list of India's 28 states + 8 union territories, with a small
alias table for common real-world variants (old names, NCT/short forms).
Anything that doesn't match is never guessed into a state -- it's
counted in `unmapped_order_count` instead (see
`SupplyIntelligenceResponse`'s docstring).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import Numeric, case, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import ShipmentStatus
from app.models.order import Order, OrderItem
from app.models.shipment import Shipment
from app.schemas.supply_intelligence import (
    CityMetric,
    MarketInsight,
    StateDetail,
    StateMetric,
    StateProductMetric,
    SupplyIntelligencePeriod,
    SupplyIntelligenceResponse,
    SupplyIntelligenceSummary,
)

DEFAULT_WINDOW_DAYS = 30

CANONICAL_STATES: list[str] = [
    "Andhra Pradesh",
    "Arunachal Pradesh",
    "Assam",
    "Bihar",
    "Chhattisgarh",
    "Goa",
    "Gujarat",
    "Haryana",
    "Himachal Pradesh",
    "Jharkhand",
    "Karnataka",
    "Kerala",
    "Madhya Pradesh",
    "Maharashtra",
    "Manipur",
    "Meghalaya",
    "Mizoram",
    "Nagaland",
    "Odisha",
    "Punjab",
    "Rajasthan",
    "Sikkim",
    "Tamil Nadu",
    "Telangana",
    "Tripura",
    "Uttar Pradesh",
    "Uttarakhand",
    "West Bengal",
    "Andaman and Nicobar Islands",
    "Chandigarh",
    "Dadra and Nagar Haveli and Daman and Diu",
    "Delhi",
    "Jammu and Kashmir",
    "Ladakh",
    "Lakshadweep",
    "Puducherry",
]

# lowercased alias -> canonical name, for common real-world variants that
# aren't a plain casing difference from the list above.
_STATE_ALIASES: dict[str, str] = {
    "orissa": "Odisha",
    "pondicherry": "Puducherry",
    "nct of delhi": "Delhi",
    "new delhi": "Delhi",
    "delhi ncr": "Delhi",
    "uttaranchal": "Uttarakhand",
    "jammu & kashmir": "Jammu and Kashmir",
    "j&k": "Jammu and Kashmir",
    "dadra and nagar haveli": "Dadra and Nagar Haveli and Daman and Diu",
    "daman and diu": "Dadra and Nagar Haveli and Daman and Diu",
}

_CANONICAL_LOOKUP: dict[str, str] = {name.lower(): name for name in CANONICAL_STATES}

# Growth/RTO thresholds behind the opportunity classification (section 8
# of the spec) -- fixed, documented percentages rather than an invented
# per-dataset statistic, so the classification is reproducible and
# explainable in a demo. "High demand" is relative to the dataset (the
# mean order count across active states), since an absolute order-count
# cutoff would be meaningless across different data volumes.
_HIGH_GROWTH_THRESHOLD_PCT = 20.0
_HIGH_RTO_THRESHOLD_PCT = 10.0
_UNTAPPED_ORDER_CEILING = 5


def normalize_state(raw: str | None) -> str | None:
    """Maps free-text `shipping_address['state']` to a canonical Indian
    state/UT name, or `None` if it doesn't match anything known -- never
    guessed, so an unrecognized value is dropped into
    `unmapped_order_count` by the caller instead of silently
    misattributed.
    """
    if not raw:
        return None
    cleaned = raw.strip().lower()
    if not cleaned:
        return None
    if cleaned in _STATE_ALIASES:
        return _STATE_ALIASES[cleaned]
    return _CANONICAL_LOOKUP.get(cleaned)


def _raw_variants_for(canonical: str) -> list[str]:
    """Every lowercased raw string (the canonical name itself, plus any
    alias) that normalizes to `canonical` -- used to filter SQL by
    `func.lower(state_expr).in_(...)` when a caller selects one state, so
    an order stored as "Orissa" still matches a request for "Odisha".
    """
    variants = [canonical.lower()]
    variants.extend(alias for alias, target in _STATE_ALIASES.items() if target == canonical)
    return variants


@dataclass(frozen=True)
class DateRange:
    date_from: datetime
    date_to: datetime


def _resolve_range(date_from: datetime | None, date_to: datetime | None) -> DateRange:
    if date_to is None:
        date_to = datetime.now(UTC)
    if date_from is None:
        date_from = date_to - timedelta(days=DEFAULT_WINDOW_DAYS)
    return DateRange(date_from=date_from, date_to=date_to)


def _previous_range(current: DateRange) -> DateRange:
    span = current.date_to - current.date_from
    return DateRange(date_from=current.date_from - span, date_to=current.date_from)


def _growth_pct(current: int, previous: int) -> float | None:
    if previous == 0:
        return None
    return (current - previous) / previous * 100


class SupplyIntelligenceService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_supply_intelligence(
        self,
        date_from: datetime | None,
        date_to: datetime | None,
        selected_state: str | None,
    ) -> SupplyIntelligenceResponse:
        current = _resolve_range(date_from, date_to)
        previous = _previous_range(current)

        order_rows, unmapped_orders = await self._state_order_metrics(current)
        orders_by_state = {row["state"]: row for row in order_rows}
        previous_order_rows, _ = await self._state_order_metrics(previous)
        previous_orders_by_state = {row["state"]: row["orders"] for row in previous_order_rows}
        shipments_by_state = await self._state_shipment_metrics(current)

        active_order_counts = [row["orders"] for row in order_rows if row["orders"] > 0]
        mean_active_orders = (
            sum(active_order_counts) / len(active_order_counts) if active_order_counts else 0.0
        )

        # Every canonical state/UT is represented, even with zero orders --
        # "Untapped Markets" (spec section 7) and the map's "No order
        # data" state (section 3) both need to know about states with
        # NO activity, not just the ones present in the order data.
        states: list[StateMetric] = []
        for state in CANONICAL_STATES:
            row = orders_by_state.get(
                state, {"state": state, "orders": 0, "revenue": Decimal("0"), "customers": 0}
            )
            shipment_row = shipments_by_state.get(
                state,
                {"total": 0, "delivered": 0, "in_transit": 0, "pending": 0, "rto": 0, "ndr": 0},
            )
            total_shipments = shipment_row["total"]
            rto_rate_pct = (
                (shipment_row["rto"] / total_shipments * 100) if total_shipments else None
            )
            growth_pct = _growth_pct(row["orders"], previous_orders_by_state.get(state, 0))
            states.append(
                StateMetric(
                    state=state,
                    orders=row["orders"],
                    revenue=row["revenue"],
                    customers=row["customers"],
                    delivered=shipment_row["delivered"],
                    in_transit=shipment_row["in_transit"],
                    pending=shipment_row["pending"],
                    rto=shipment_row["rto"],
                    ndr=shipment_row["ndr"],
                    rto_rate_pct=rto_rate_pct,
                    growth_pct=growth_pct,
                    opportunity=_classify_opportunity(
                        orders=row["orders"],
                        mean_active_orders=mean_active_orders,
                        growth_pct=growth_pct,
                        rto_rate_pct=rto_rate_pct,
                    ),
                )
            )
        states.sort(key=lambda s: s.orders, reverse=True)

        summary = _build_summary(states)
        insights = _build_insights(states)

        selected_detail = None
        if selected_state:
            canonical = normalize_state(selected_state) or selected_state
            match = next((s for s in states if s.state == canonical), None)
            if match is not None:
                selected_detail = await self._state_detail(current, canonical, match)

        return SupplyIntelligenceResponse(
            summary=summary,
            states=states,
            selected_state=selected_detail,
            insights=insights,
            period=SupplyIntelligencePeriod(date_from=current.date_from, date_to=current.date_to),
            comparison_period=SupplyIntelligencePeriod(
                date_from=previous.date_from, date_to=previous.date_to
            ),
            unmapped_order_count=unmapped_orders,
        )

    async def _state_order_metrics(
        self, r: DateRange
    ) -> tuple[list[dict], int]:
        """Groups orders in range by raw `shipping_address['state']` text,
        then merges those raw groups into canonical states in Python (a
        handful of distinct raw strings at most -- cheap). Rows that
        don't normalize to a known state are summed into `unmapped`
        rather than dropped silently.
        """
        state_expr = Order.shipping_address["state"].as_string()
        stmt = (
            select(
                state_expr.label("state_raw"),
                func.count(Order.id),
                func.coalesce(func.sum(Order.total_amount), 0),
                func.count(func.distinct(Order.customer_id)),
            )
            .where(Order.order_datetime >= r.date_from, Order.order_datetime <= r.date_to)
            .group_by(state_expr)
        )
        rows = (await self.session.execute(stmt)).all()

        by_state: dict[str, dict] = {}
        unmapped = 0
        for state_raw, orders, revenue, customers in rows:
            canonical = normalize_state(state_raw)
            if canonical is None:
                unmapped += orders
                continue
            bucket = by_state.setdefault(
                canonical,
                {"state": canonical, "orders": 0, "revenue": Decimal("0"), "customers": 0},
            )
            bucket["orders"] += orders
            bucket["revenue"] += Decimal(revenue)
            # Distinct-customer counts from different raw-state groups
            # can't just be summed (a customer could theoretically appear
            # under two raw spellings) -- summing is a reasonable
            # approximation given how rare that is in practice, and never
            # UNDER-counts each individual group's real distinct total.
            bucket["customers"] += customers

        return list(by_state.values()), unmapped

    async def _state_shipment_metrics(self, r: DateRange) -> dict[str, dict]:
        """Delivered/in-transit/pending/RTO/NDR counts per state, scoped
        by the *order's* placement date (not the shipment's own
        created_at) -- keeps this consistent with the order-side
        aggregation above, which is what the page's date-range picker
        actually filters.
        """
        state_expr = Order.shipping_address["state"].as_string()
        delivered_case = case((Shipment.current_status == ShipmentStatus.DELIVERED, 1), else_=0)
        in_transit_case = case(
            (Shipment.current_status.in_([ShipmentStatus.PICKED_UP, ShipmentStatus.IN_TRANSIT]), 1),
            else_=0,
        )
        pending_case = case((Shipment.current_status == ShipmentStatus.PENDING, 1), else_=0)
        ndr_case = case((Shipment.current_status == ShipmentStatus.NDR, 1), else_=0)
        rto_case = case(
            (
                Shipment.current_status.in_(
                    [ShipmentStatus.RTO_INITIATED, ShipmentStatus.RTO_DELIVERED]
                ),
                1,
            ),
            else_=0,
        )
        stmt = (
            select(
                state_expr.label("state_raw"),
                func.count(Shipment.id),
                func.sum(cast(delivered_case, Numeric)),
                func.sum(cast(in_transit_case, Numeric)),
                func.sum(cast(pending_case, Numeric)),
                func.sum(cast(rto_case, Numeric)),
                func.sum(cast(ndr_case, Numeric)),
            )
            .join(Order, Order.id == Shipment.order_id)
            .where(Order.order_datetime >= r.date_from, Order.order_datetime <= r.date_to)
            .group_by(state_expr)
        )
        rows = (await self.session.execute(stmt)).all()

        by_state: dict[str, dict] = {}
        for state_raw, total, delivered, in_transit, pending, rto, ndr in rows:
            canonical = normalize_state(state_raw)
            if canonical is None:
                continue
            bucket = by_state.setdefault(
                canonical,
                {"total": 0, "delivered": 0, "in_transit": 0, "pending": 0, "rto": 0, "ndr": 0},
            )
            bucket["total"] += total or 0
            bucket["delivered"] += int(delivered or 0)
            bucket["in_transit"] += int(in_transit or 0)
            bucket["pending"] += int(pending or 0)
            bucket["rto"] += int(rto or 0)
            bucket["ndr"] += int(ndr or 0)
        return by_state

    async def _state_detail(
        self, r: DateRange, canonical_state: str, metric: StateMetric
    ) -> StateDetail:
        variants = _raw_variants_for(canonical_state)
        state_expr = Order.shipping_address["state"].as_string()
        city_expr = Order.shipping_address["city"].as_string()

        cities_stmt = (
            select(
                city_expr.label("city"),
                func.count(Order.id),
                func.coalesce(func.sum(Order.total_amount), 0),
            )
            .where(
                Order.order_datetime >= r.date_from,
                Order.order_datetime <= r.date_to,
                func.lower(state_expr).in_(variants),
            )
            .group_by(city_expr)
            .order_by(func.count(Order.id).desc())
            .limit(10)
        )
        city_rows = (await self.session.execute(cities_stmt)).all()
        cities = [
            CityMetric(city=city or "Unknown", orders=orders, revenue=Decimal(revenue))
            for city, orders, revenue in city_rows
        ]

        products_stmt = (
            select(
                OrderItem.sku,
                OrderItem.product_name,
                func.count(func.distinct(OrderItem.order_id)),
                func.coalesce(func.sum(OrderItem.quantity), 0),
                func.coalesce(func.sum(OrderItem.total_amount), 0),
            )
            .join(Order, Order.id == OrderItem.order_id)
            .where(
                Order.order_datetime >= r.date_from,
                Order.order_datetime <= r.date_to,
                func.lower(state_expr).in_(variants),
            )
            .group_by(OrderItem.sku, OrderItem.product_name)
            .order_by(func.sum(OrderItem.quantity).desc())
            .limit(15)
        )
        product_rows = (await self.session.execute(products_stmt)).all()
        products = [
            StateProductMetric(
                sku=sku or "—",
                product_name=product_name,
                orders=orders,
                quantity=int(quantity or 0),
                revenue=Decimal(revenue),
                avg_order_value=(Decimal(revenue) / orders) if orders else Decimal("0"),
            )
            for sku, product_name, orders, quantity, revenue in product_rows
        ]

        return StateDetail(
            state=canonical_state,
            orders=metric.orders,
            revenue=metric.revenue,
            avg_order_value=(metric.revenue / metric.orders) if metric.orders else Decimal("0"),
            customers=metric.customers,
            delivered=metric.delivered,
            in_transit=metric.in_transit,
            pending=metric.pending,
            rto=metric.rto,
            ndr=metric.ndr,
            rto_rate_pct=metric.rto_rate_pct,
            cities=cities,
            products=products,
        )


def _classify_opportunity(
    *,
    orders: int,
    mean_active_orders: float,
    growth_pct: float | None,
    rto_rate_pct: float | None,
) -> str:
    """Deterministic, threshold-based classification (spec section 8) --
    no per-dataset tuning, no ML, just the documented rules below:

      - `untapped`: orders <= `_UNTAPPED_ORDER_CEILING` (a state with
        essentially no activity yet).
      - `investigate`: at-or-above-average demand for this dataset AND
        RTO rate >= `_HIGH_RTO_THRESHOLD_PCT` -- high volume with a
        logistics problem worth a human look.
      - `scale`: at-or-above-average demand AND not flagged for RTO,
        i.e. a proven, healthy market.
      - `opportunity`: below-average demand but growth >=
        `_HIGH_GROWTH_THRESHOLD_PCT` -- small today, growing fast.
      - `steady`: everything else (below-average demand, no notable
        growth, no RTO signal) -- deliberately not forced into one of
        the four headline buckets above; "nothing notable to flag" is a
        real, honest outcome, not a gap in the classification.
    """
    if orders <= _UNTAPPED_ORDER_CEILING:
        return "untapped"

    is_high_demand = orders >= mean_active_orders
    is_high_rto = rto_rate_pct is not None and rto_rate_pct >= _HIGH_RTO_THRESHOLD_PCT
    is_high_growth = growth_pct is not None and growth_pct >= _HIGH_GROWTH_THRESHOLD_PCT

    if is_high_demand and is_high_rto:
        return "investigate"
    if is_high_demand:
        return "scale"
    if is_high_growth:
        return "opportunity"
    return "steady"


def _build_summary(states: list[StateMetric]) -> SupplyIntelligenceSummary:
    active = [s for s in states if s.orders > 0]
    top_state = max(active, key=lambda s: s.orders).state if active else None
    top_revenue_state = max(active, key=lambda s: s.revenue).state if active else None
    return SupplyIntelligenceSummary(
        total_orders=sum(s.orders for s in states),
        total_revenue=sum((s.revenue for s in states), Decimal("0")),
        active_states=len(active),
        top_state=top_state,
        top_revenue_state=top_revenue_state,
    )


def _build_insights(states: list[StateMetric]) -> list[MarketInsight]:
    """Every insight below is computed straight from `states` -- no
    invented copy, no insight emitted when the underlying data can't
    support it (spec section 7: "If insufficient data exists, display
    'Not enough data to calculate this insight.'").
    """
    insights: list[MarketInsight] = []
    active = [s for s in states if s.orders > 0]

    if active:
        strongest = max(active, key=lambda s: s.orders)
        insights.append(
            MarketInsight(
                type="strongest_market",
                title="Strongest Market",
                description=f"{strongest.state} currently contributes the highest order volume "
                f"({strongest.orders:,} orders in the selected period).",
                states=[strongest.state],
            )
        )
    else:
        insights.append(
            MarketInsight(
                type="strongest_market",
                title="Strongest Market",
                description="Not enough data to calculate this insight.",
                states=[],
            )
        )

    growth_candidates = [s for s in active if s.growth_pct is not None]
    if growth_candidates:
        fastest = max(growth_candidates, key=lambda s: s.growth_pct or 0)
        if (fastest.growth_pct or 0) > 0:
            insights.append(
                MarketInsight(
                    type="fastest_growing",
                    title="Fastest Growing Market",
                    description=f"{fastest.state} is showing the strongest growth "
                    f"({fastest.growth_pct:+.1f}% vs. the previous comparable period).",
                    states=[fastest.state],
                )
            )
        else:
            insights.append(
                MarketInsight(
                    type="fastest_growing",
                    title="Fastest Growing Market",
                    description="No state grew versus the previous comparable period.",
                    states=[],
                )
            )
    else:
        insights.append(
            MarketInsight(
                type="fastest_growing",
                title="Fastest Growing Market",
                description="Not enough data to calculate this insight.",
                states=[],
            )
        )

    emerging = [s for s in states if s.opportunity == "opportunity"]
    if emerging:
        best = max(emerging, key=lambda s: s.growth_pct or 0)
        insights.append(
            MarketInsight(
                type="emerging_market",
                title="Emerging Market",
                description=f"{best.state} has a smaller order base but is growing quickly "
                f"({best.growth_pct:+.1f}%) -- worth watching.",
                states=[best.state],
            )
        )
    else:
        insights.append(
            MarketInsight(
                type="emerging_market",
                title="Emerging Market",
                description="Not enough data to calculate this insight.",
                states=[],
            )
        )

    attention = [s for s in states if s.opportunity == "investigate"]
    if attention:
        worst = max(attention, key=lambda s: s.rto_rate_pct or 0)
        insights.append(
            MarketInsight(
                type="attention_required",
                title="Attention Required",
                description=f"{worst.state} combines high order volume with an elevated RTO rate "
                f"({worst.rto_rate_pct:.1f}%) -- may warrant a logistics review.",
                states=[s.state for s in attention],
            )
        )
    else:
        insights.append(
            MarketInsight(
                type="attention_required",
                title="Attention Required",
                description="No high-volume state currently shows an elevated RTO rate.",
                states=[],
            )
        )

    untapped = [s for s in states if s.opportunity == "untapped"]
    if untapped:
        insights.append(
            MarketInsight(
                type="untapped_markets",
                title="Untapped Markets",
                description=f"{len(untapped)} state(s)/UT(s) have little to no order activity "
                "in this period.",
                states=[s.state for s in untapped],
            )
        )
    else:
        insights.append(
            MarketInsight(
                type="untapped_markets",
                title="Untapped Markets",
                description="Not enough data to calculate this insight.",
                states=[],
            )
        )

    return insights

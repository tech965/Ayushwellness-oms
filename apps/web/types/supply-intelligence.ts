// Mirrors app/schemas/supply_intelligence.py

export type OpportunityBucket = "scale" | "opportunity" | "investigate" | "untapped" | "steady"

export interface StateMetric {
  state: string
  orders: number
  revenue: string
  customers: number
  delivered: number
  in_transit: number
  pending: number
  rto: number
  ndr: number
  rto_rate_pct: number | null
  growth_pct: number | null
  opportunity: OpportunityBucket
}

export interface CityMetric {
  city: string
  orders: number
  revenue: string
}

export interface StateProductMetric {
  sku: string
  product_name: string
  orders: number
  quantity: number
  revenue: string
  avg_order_value: string
}

export interface StateDetail {
  state: string
  orders: number
  revenue: string
  avg_order_value: string
  customers: number
  delivered: number
  in_transit: number
  pending: number
  rto: number
  ndr: number
  rto_rate_pct: number | null
  cities: CityMetric[]
  products: StateProductMetric[]
}

export interface SupplyIntelligenceSummary {
  total_orders: number
  total_revenue: string
  active_states: number
  top_state: string | null
  top_revenue_state: string | null
}

export type MarketInsightType =
  | "strongest_market"
  | "fastest_growing"
  | "emerging_market"
  | "attention_required"
  | "untapped_markets"

export interface MarketInsight {
  type: MarketInsightType
  title: string
  description: string
  states: string[]
}

export interface SupplyIntelligencePeriod {
  date_from: string
  date_to: string
}

export interface SupplyIntelligenceResponse {
  summary: SupplyIntelligenceSummary
  states: StateMetric[]
  selected_state: StateDetail | null
  insights: MarketInsight[]
  period: SupplyIntelligencePeriod
  comparison_period: SupplyIntelligencePeriod
  unmapped_order_count: number
}

export interface SupplyIntelligenceParams {
  date_from?: string
  date_to?: string
  state?: string
}

export type MapMetric = "orders" | "revenue" | "customers" | "rto_rate_pct"

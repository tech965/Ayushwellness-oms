// Mirrors app/schemas/operations_command_center.py

export type Priority = "critical" | "warning" | "opportunity" | "positive"

export interface CommandCenterSummary {
  total_orders: number
  orders_growth_pct: number | null
  total_revenue: string
  requires_attention_count: number
}

export interface AttentionItem {
  type: string
  label: string
  count: number
  amount: string | null
  priority: Priority
  href: string
}

export interface MetricPair {
  label: string
  value: number | null
}

export interface OperationsHealth {
  orders: MetricPair[]
  payments: MetricPair[]
  shipments: MetricPair[]
  returns: MetricPair[]
  refunds: MetricPair[]
}

export interface BusinessOpportunity {
  type: string
  title: string
  description: string
}

export interface CommandCenterInsight {
  priority: Priority
  message: string
}

export interface CommandCenterPeriod {
  date_from: string
  date_to: string
}

export interface OperationsCommandCenterResponse {
  summary: CommandCenterSummary
  attention_items: AttentionItem[]
  operations_health: OperationsHealth
  business_opportunities: BusinessOpportunity[]
  insights: CommandCenterInsight[]
  period: CommandCenterPeriod
  comparison_period: CommandCenterPeriod
}

export interface CommandCenterParams {
  date_from?: string
  date_to?: string
}

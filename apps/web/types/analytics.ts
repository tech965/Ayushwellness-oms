export interface KPIValue {
  current: string
  previous: string
  change_pct: number | null
}

export interface AnalyticsSummary {
  date_from: string
  date_to: string
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
}

export type TimeseriesInterval = "hour" | "day" | "week" | "month"

export interface TimeseriesPoint {
  bucket: string
  order_count: number
  revenue: string
}

export interface OrdersTimeseries {
  interval: TimeseriesInterval
  points: TimeseriesPoint[]
}

export interface StatusCount {
  status: string
  count: number
}

export interface Breakdowns {
  order_status: StatusCount[]
  payment_type: StatusCount[]
  payment_status: StatusCount[]
  fulfillment_status: StatusCount[]
  shipment_status: StatusCount[]
}

export interface TopProduct {
  sku: string
  title: string
  units_sold: number
  revenue: string
}

export interface CourierPerformance {
  courier_id: string
  name: string
  shipment_count: number
  delivered_count: number
  in_transit_count: number
  pending_count: number
  ndr_count: number
  rto_count: number
  delivered_pct: number
  ndr_pct: number
  rto_pct: number
}

export interface RecentOrder {
  id: string
  order_number: string
  total_amount: string
  status: string
  created_at: string
}

export interface RecentShipment {
  id: string
  order_id: string
  awb: string | null
  current_status: string
  updated_at: string
}

export interface RecentNdrRto {
  id: string
  order_id: string
  kind: "ndr" | "rto"
  status: string
  reason: string | null
  created_at: string
}

export interface RecentPayment {
  id: string
  order_id: string
  amount: string
  status: string
  created_at: string
}

export interface RecentActivity {
  recent_orders: RecentOrder[]
  recent_shipments: RecentShipment[]
  recent_ndr_rto: RecentNdrRto[]
  recent_payments: RecentPayment[]
}

export interface AnalyticsDateRangeParams {
  date_from?: string
  date_to?: string
}

// --- Revenue/order drill-down analytics (Total Revenue/Total Orders ->
// COD/Prepaid -> Paid/Pending). "paid" = payment_status "paid"; "pending"
// = every other payment_status -- see AnalyticsService's docstring
// (backend) for the authoritative definition these mirror.

export interface PaymentStatusBreakdownItem {
  status: string
  count: number
  revenue: string
}

export interface PaymentStatusBreakdown {
  payment_type: "cod" | "prepaid" | null
  total_count: number
  total_revenue: string
  paid_count: number
  paid_revenue: string
  pending_count: number
  pending_revenue: string
  items: PaymentStatusBreakdownItem[]
}

export interface RevenueTimeseriesPoint {
  bucket: string
  cod_orders: number
  cod_revenue: string
  prepaid_orders: number
  prepaid_revenue: string
  total_orders: number
  total_revenue: string
}

export interface RevenueTimeseries {
  interval: TimeseriesInterval
  points: RevenueTimeseriesPoint[]
}

export interface PaymentStatusTimeseriesPoint {
  bucket: string
  paid_orders: number
  paid_revenue: string
  pending_orders: number
  pending_revenue: string
  total_orders: number
  total_revenue: string
}

export interface PaymentStatusTimeseries {
  interval: TimeseriesInterval
  payment_type: "cod" | "prepaid"
  points: PaymentStatusTimeseriesPoint[]
}

export interface ReturnsSummary {
  total_returns: number
  pending_returns: number
  completed_returns: number
  return_rate_pct: number | null
}

export interface RefundsSummary {
  total_refunds: number
  total_refund_amount: string
  pending_refunds: number
  completed_refunds: number
}

export interface ReturnsRefundsSummary {
  returns: ReturnsSummary
  refunds: RefundsSummary
}

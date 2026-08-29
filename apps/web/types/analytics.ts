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

export type TimeseriesInterval = "day" | "week" | "month"

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

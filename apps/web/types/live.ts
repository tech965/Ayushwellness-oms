/** Mirrors apps/api/app/services/live_analytics_service.py. Money values
 * arrive as decimal strings. */

export interface LiveLabelCount {
  label: string
  count: number
  amount: string
}

export interface LiveDailyPoint {
  date: string
  orders: number
  revenue: string
}

export interface LiveProductSales {
  sku: string
  product_name: string
  quantity: number
  revenue: string
}

export interface LiveAnalyticsSummary {
  /** False when the scan stopped early: totals then cover only
   * `orders_scanned` orders and must not be read as global totals. */
  complete: boolean
  orders_scanned: number
  warnings: string[]
  total_orders: number
  cancelled_orders: number
  revenue: string
  average_order_value: string
  by_fulfillment_status: LiveLabelCount[]
  by_payment_type: LiveLabelCount[]
  by_payment_status: LiveLabelCount[]
  by_shopify_delivery_status: LiveLabelCount[]
  daily: LiveDailyPoint[]
  top_products: LiveProductSales[]
  confirmed_orders: number
  assigned_orders: number
  unassigned_orders: number
  /** Figures that genuinely cannot be derived from the provider APIs yet. */
  unavailable: string[]
}

// Mirrors app/schemas/dashboard.py

export interface DashboardSummary {
  total_orders: number
  total_customers: number
  total_products: number
  total_shipments: number
  delivered_shipments: number
  delayed_shipments: number
  open_ndr_count: number
  open_rto_count: number
}

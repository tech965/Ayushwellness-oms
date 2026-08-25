// Mirrors app/schemas/customer.py

export interface Customer {
  id: string
  shopify_customer_id: string | null
  first_name: string | null
  last_name: string | null
  full_name: string | null
  email: string | null
  phone: string | null
  alternate_phone: string | null
  is_active: boolean
  notes: string | null
  source_system: string | null
  external_id: string | null
  last_synced_at: string | null
  created_at: string
  updated_at: string
}

export interface CustomerSummary {
  customer_id: string
  total_orders: number
  total_spent: string
  delivered_orders: number
  cancelled_orders: number
  rto_orders: number
  average_order_value: string
  last_order_at: string | null
}

export interface CustomerListFilters {
  q?: string
}

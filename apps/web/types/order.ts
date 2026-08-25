// Mirrors app/schemas/order.py

export type OrderStatus =
  | "pending"
  | "confirmed"
  | "processing"
  | "packed"
  | "shipped"
  | "delivered"
  | "cancelled"

export type PaymentStatus =
  "pending" | "authorized" | "paid" | "failed" | "refunded" | "partially_refunded"

export type PaymentType = "cod" | "prepaid" | "other"
export type FulfillmentStatus = "unfulfilled" | "partial" | "fulfilled"
export type CancellationStatus = "none" | "requested" | "cancelled"

export interface OrderItem {
  id: string
  order_id: string
  product_variant_id: string | null
  sku: string
  product_name: string
  quantity: number
  unit_price: string
  discount_amount: string
  tax_amount: string
  total_amount: string
}

export interface Order {
  id: string
  order_number: string
  shopify_order_id: string | null
  customer_id: string | null
  order_datetime: string
  currency: string
  subtotal: string
  discount_amount: string
  tax_amount: string
  shipping_charge: string
  total_amount: string
  payment_type: PaymentType
  payment_status: PaymentStatus
  status: OrderStatus
  fulfillment_status: FulfillmentStatus
  cancellation_status: CancellationStatus
  notes: string | null
  shipping_address: OrderAddress | null
  billing_address: OrderAddress | null
  source_system: string | null
  created_at: string
  updated_at: string
}

export interface OrderAddress {
  line1: string
  line2: string | null
  city: string
  state: string | null
  country: string
  pin_code: string
  contact_name: string | null
  contact_phone: string | null
  is_default: boolean
}

export interface OrderDetail extends Order {
  items: OrderItem[]
}

export interface OrderEvent {
  id: string
  order_id: string
  event_type: string
  status: string | null
  description: string | null
  source: string
  actor_user_id: string | null
  event_metadata: Record<string, unknown> | null
  created_at: string
}

export interface OrderListFilters {
  q?: string
  status?: OrderStatus
  payment_status?: PaymentStatus
  date_from?: string
  date_to?: string
}

export const ORDER_STATUS_OPTIONS: { label: string; value: OrderStatus }[] = [
  { label: "Pending", value: "pending" },
  { label: "Confirmed", value: "confirmed" },
  { label: "Processing", value: "processing" },
  { label: "Packed", value: "packed" },
  { label: "Shipped", value: "shipped" },
  { label: "Delivered", value: "delivered" },
  { label: "Cancelled", value: "cancelled" },
]

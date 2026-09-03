// Mirrors app/schemas/order.py

import type { Customer } from "./customer"

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
  // Shopify-owned tags/order note — distinct from `notes` above, which
  // is OMS-internal staff text set at manual-order-creation time and
  // never touched by Shopify sync. `null` on `shopify_tags` means "this
  // order has never synced from Shopify" (e.g. a manually created
  // order); every Shopify-synced order always has a real (possibly
  // empty) array.
  shopify_tags: string[] | null
  shopify_order_note: string | null
  shipping_address: OrderAddress | null
  billing_address: OrderAddress | null
  source_system: string | null
  created_at: string
  updated_at: string
  // Present only on rows from `GET /orders` (`OrderListResponse`) — see
  // `_to_list_response` in the backend endpoint. Absent (undefined) on
  // plain `OrderResponse` rows, e.g. a customer's order history.
  customer_name?: string | null
  customer_phone?: string | null
  customer_email?: string | null
  item_summary?: string | null
  total_quantity?: number
  shipment_status?: string | null
  courier_name?: string | null
  tracking_number?: string | null
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
  customer: Customer | null
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
  payment_type?: PaymentType
  fulfillment_status?: FulfillmentStatus
  shipment_status?: string
  courier_id?: string
  sku?: string
  tag?: string
  amount_min?: string
  amount_max?: string
  date_from?: string
  date_to?: string
}

export const PAYMENT_TYPE_OPTIONS: { label: string; value: PaymentType }[] = [
  { label: "COD", value: "cod" },
  { label: "Prepaid", value: "prepaid" },
  { label: "Other", value: "other" },
]

export const FULFILLMENT_STATUS_OPTIONS: { label: string; value: FulfillmentStatus }[] = [
  { label: "Fulfilled", value: "fulfilled" },
  { label: "Unfulfilled", value: "unfulfilled" },
  { label: "Partial", value: "partial" },
]

export const PAYMENT_STATUS_OPTIONS: { label: string; value: PaymentStatus }[] = [
  { label: "Pending", value: "pending" },
  { label: "Authorized", value: "authorized" },
  { label: "Paid", value: "paid" },
  { label: "Failed", value: "failed" },
  { label: "Refunded", value: "refunded" },
  { label: "Partially Refunded", value: "partially_refunded" },
]

export const ORDER_STATUS_OPTIONS: { label: string; value: OrderStatus }[] = [
  { label: "Pending", value: "pending" },
  { label: "Confirmed", value: "confirmed" },
  { label: "Processing", value: "processing" },
  { label: "Packed", value: "packed" },
  { label: "Shipped", value: "shipped" },
  { label: "Delivered", value: "delivered" },
  { label: "Cancelled", value: "cancelled" },
]

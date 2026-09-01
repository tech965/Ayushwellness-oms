// Mirrors app/schemas/payment.py

export type PaymentStatus =
  "pending" | "authorized" | "paid" | "failed" | "refunded" | "partially_refunded"

export type PaymentTransaction = {
  id: string
  payment_id: string
  gateway: string | null
  gateway_transaction_id: string | null
  status: PaymentStatus
  amount: string
  created_at: string
  event_type: string | null
  payment_method: string | null
  error_reason: string | null
}

export interface Payment {
  id: string
  order_id: string
  // Denormalized from the linked order/customer (see
  // `PaymentRepository._WITH_ORDER_AND_CUSTOMER`) — present on every
  // response, never a separate lookup.
  order_number: string | null
  customer_name: string | null
  customer_phone: string | null
  customer_email: string | null
  payment_type: "cod" | "prepaid" | "other"
  status: PaymentStatus
  amount: string
  currency: string
  provider: string | null
  source_system: string | null
  // The gateway's own order id (Cashfree: `cashfree_order_id`) — only
  // ever populated for a gateway-backed payment.
  external_id: string | null
  external_transaction_id: string | null
  payment_session_id: string | null
  payment_method: string | null
  paid_at: string | null
  created_at: string
  updated_at: string
}

export interface PaymentDetail extends Payment {
  transactions: PaymentTransaction[]
}

export interface PaymentListFilters {
  order_id?: string
  provider?: string
  status?: PaymentStatus
  payment_method?: string
  q?: string
  date_from?: string
  date_to?: string
}

export const PAYMENT_STATUS_OPTIONS: { label: string; value: PaymentStatus }[] = [
  { label: "Pending", value: "pending" },
  { label: "Authorized", value: "authorized" },
  { label: "Paid", value: "paid" },
  { label: "Failed", value: "failed" },
  { label: "Refunded", value: "refunded" },
  { label: "Partially Refunded", value: "partially_refunded" },
]

export const PAYMENT_PROVIDER_OPTIONS: { label: string; value: string }[] = [
  { label: "Cashfree", value: "cashfree" },
]

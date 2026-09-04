// Mirrors app/schemas/refund.py

export type RefundStatus = "pending" | "processing" | "completed" | "failed" | "cancelled"

export interface Refund {
  id: string
  order_id: string
  payment_id: string | null
  return_id: string | null
  amount: string
  reason: string | null
  status: RefundStatus
  initiated_at: string | null
  completed_at: string | null
  source_system: string | null
  created_at: string
  updated_at: string
  // Denormalized order/customer/product columns — present only on rows
  // from `GET /refunds` (`RefundListResponse`). `order_amount` is the
  // ORIGINAL order total, deliberately distinct from `amount` above (the
  // refund amount) — never conflate the two.
  order_number?: string | null
  customer_name?: string | null
  customer_phone?: string | null
  product?: string | null
  order_amount?: string | null
  payment_type?: string | null
}

export interface RefundListFilters {
  q?: string
  status?: RefundStatus
  payment_type?: string
  order_id?: string
  date_from?: string
  date_to?: string
}

export const REFUND_STATUS_OPTIONS: { label: string; value: RefundStatus }[] = [
  { label: "Pending", value: "pending" },
  { label: "Processing", value: "processing" },
  { label: "Completed", value: "completed" },
  { label: "Failed", value: "failed" },
  { label: "Cancelled", value: "cancelled" },
]

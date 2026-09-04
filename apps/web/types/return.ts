// Mirrors app/schemas/returns.py

export type ReturnStatus =
  | "requested"
  | "approved"
  | "rejected"
  | "in_transit"
  | "received"
  | "completed"
  | "cancelled"

export interface Return {
  id: string
  order_id: string
  order_item_id: string | null
  customer_id: string | null
  reason: string | null
  status: ReturnStatus
  quantity: number
  requested_at: string | null
  approved_at: string | null
  received_at: string | null
  completed_at: string | null
  notes: string | null
  source_system: string | null
  created_at: string
  updated_at: string
  // Denormalized order/customer/product columns — present only on rows
  // from `GET /returns` (`ReturnListResponse`).
  order_number?: string | null
  customer_name?: string | null
  customer_phone?: string | null
  product?: string | null
  order_amount?: string | null
  payment_type?: string | null
}

export interface ReturnListFilters {
  q?: string
  status?: ReturnStatus
  payment_type?: string
  customer_id?: string
  order_id?: string
  date_from?: string
  date_to?: string
}

export const RETURN_STATUS_OPTIONS: { label: string; value: ReturnStatus }[] = [
  { label: "Requested", value: "requested" },
  { label: "Approved", value: "approved" },
  { label: "Rejected", value: "rejected" },
  { label: "In transit", value: "in_transit" },
  { label: "Received", value: "received" },
  { label: "Completed", value: "completed" },
  { label: "Cancelled", value: "cancelled" },
]

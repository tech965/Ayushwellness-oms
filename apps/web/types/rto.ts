// Mirrors app/schemas/rto.py

export type RTOStatus = "initiated" | "in_transit" | "received" | "cancelled"

export interface RTO {
  id: string
  shipment_id: string
  order_id: string
  courier_id: string | null
  reason: string | null
  normalized_reason: string | null
  external_reason: string | null
  status: RTOStatus
  initiated_at: string | null
  completed_at: string | null
  notes: string | null
  source_system: string | null
  created_at: string
  updated_at: string
  // Denormalized order/customer/product/shipment columns — present only
  // on rows from `GET /rto` (`RTOListResponse`).
  order_number?: string | null
  customer_name?: string | null
  customer_phone?: string | null
  product?: string | null
  order_amount?: string | null
  payment_type?: string | null
  shipment_status?: string | null
  awb?: string | null
  courier_name?: string | null
}

export interface RTOListFilters {
  q?: string
  status?: RTOStatus
  payment_type?: string
  courier_id?: string
  date_from?: string
  date_to?: string
}

export const RTO_STATUS_OPTIONS: { label: string; value: RTOStatus }[] = [
  { label: "Initiated", value: "initiated" },
  { label: "In transit", value: "in_transit" },
  { label: "Received", value: "received" },
  { label: "Cancelled", value: "cancelled" },
]

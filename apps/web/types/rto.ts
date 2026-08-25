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
}

export interface RTOListFilters {
  status?: RTOStatus
  courier_id?: string
}

export const RTO_STATUS_OPTIONS: { label: string; value: RTOStatus }[] = [
  { label: "Initiated", value: "initiated" },
  { label: "In transit", value: "in_transit" },
  { label: "Received", value: "received" },
  { label: "Cancelled", value: "cancelled" },
]

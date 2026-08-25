// Mirrors app/schemas/ndr.py

export type NDRStatus =
  "open" | "customer_contacted" | "reattempt_scheduled" | "resolved" | "rto_initiated"

export interface NDR {
  id: string
  shipment_id: string
  order_id: string
  courier_id: string | null
  reason: string | null
  normalized_reason: string | null
  external_reason: string | null
  attempt_number: number
  status: NDRStatus
  customer_response: string | null
  reattempt_status: string | null
  reattempt_date: string | null
  notes: string | null
  source_system: string | null
  created_at: string
  updated_at: string
}

export interface NDRListFilters {
  status?: NDRStatus
  courier_id?: string
}

export const NDR_STATUS_OPTIONS: { label: string; value: NDRStatus }[] = [
  { label: "Open", value: "open" },
  { label: "Customer contacted", value: "customer_contacted" },
  { label: "Reattempt scheduled", value: "reattempt_scheduled" },
  { label: "Resolved", value: "resolved" },
  { label: "RTO initiated", value: "rto_initiated" },
]

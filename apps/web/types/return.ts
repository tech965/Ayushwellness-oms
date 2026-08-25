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
}

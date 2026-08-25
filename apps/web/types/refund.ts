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
}

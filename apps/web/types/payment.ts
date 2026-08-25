// Mirrors app/schemas/payment.py

export type PaymentStatus =
  "pending" | "authorized" | "paid" | "failed" | "refunded" | "partially_refunded"

export interface Payment {
  id: string
  order_id: string
  payment_type: "cod" | "prepaid" | "other"
  status: PaymentStatus
  amount: string
  currency: string
  provider: string | null
  external_transaction_id: string | null
  paid_at: string | null
  source_system: string | null
  created_at: string
  updated_at: string
}

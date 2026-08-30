// Mirrors app/schemas/cashfree.py

import type { PaymentStatus } from "@/types/payment"

export interface CashfreeCheckout {
  payment_id: string
  order_id: string
  cashfree_order_id: string
  payment_session_id: string | null
  status: PaymentStatus
  amount: string
  currency: string
  created: boolean
  mode: "sandbox" | "production"
}

export interface CashfreePaymentStatus {
  payment_id: string
  order_id: string
  provider: string | null
  cashfree_order_id: string | null
  payment_session_id: string | null
  status: PaymentStatus
  amount: string
  currency: string
  payment_method: string | null
  created_at: string
  updated_at: string
  paid_at: string | null
}

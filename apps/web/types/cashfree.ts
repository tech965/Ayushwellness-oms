// Mirrors app/schemas/cashfree.py

import type { KPIValue, StatusCount, TimeseriesInterval } from "@/types/analytics"
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

// --- Connection status ----------------------------------------------

export type CashfreeEnvironment = "sandbox" | "production" | "not_configured"

export interface CashfreeStatus {
  configured: boolean
  environment: CashfreeEnvironment
  api_url: string | null
  api_version: string | null
}

export interface CashfreeConnectionTest {
  configured: boolean
  connected: boolean
  environment: CashfreeEnvironment
  error_type: string | null
  status_code: number | null
  checked_at: string
}

// --- Payment analytics ------------------------------------------------

export interface CashfreePaymentOverview {
  date_from: string
  date_to: string
  total_payments: KPIValue
  paid_payments: KPIValue
  pending_payments: KPIValue
  failed_payments: KPIValue
  refunded_payments: KPIValue
  total_amount: KPIValue
  pending_amount: KPIValue
  status_breakdown: StatusCount[]
}

export interface CashfreePaymentTrendPoint {
  bucket: string
  total_count: number
  total_amount: string
  paid_count: number
  paid_amount: string
  pending_count: number
  failed_count: number
}

export interface CashfreePaymentTrend {
  interval: TimeseriesInterval
  points: CashfreePaymentTrendPoint[]
}

export interface CashfreePaymentMethodBreakdownItem {
  payment_method: string
  count: number
  amount: string
}

export interface CashfreePaymentMethodBreakdown {
  items: CashfreePaymentMethodBreakdownItem[]
}

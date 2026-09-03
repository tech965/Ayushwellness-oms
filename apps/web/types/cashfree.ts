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

// --- Transaction sync (bulk, operator-triggered) -----------------------

export interface CashfreeSyncRequest {
  date_from: string
  date_to: string
}

export interface CashfreeSyncResult {
  fetched: number
  processed: number
  applied: number
  duplicates: number
  skipped: number
  failures: number
  errors: string[]
}

// --- Settlements ----------------------------------------------------

export interface CashfreeSettlementItem {
  cf_settlement_id: string
  status: string | null
  settlement_utr: string | null
  settlement_processed_on: string | null
  /** Gross transaction amount this settlement covers -- distinct from
   * `amount_settled` (PG service charge/tax/adjustments are deducted).
   */
  payment_amount: string | null
  amount_settled: string | null
}

/** Every field documented `// derived` is computed by the backend from
 * the locally-synced settlement list, not returned by a confirmed
 * dedicated Cashfree endpoint — see `CashfreeSettlementSummaryResponse`
 * in app/schemas/cashfree.py for exactly why.
 */
export interface CashfreeSettlementSummary {
  /** derived */
  unsettled_amount: string
  /** derived */
  upcoming_settlement_amount: string | null
  /** derived */
  upcoming_settlement_status: string | null
  last_settled_amount: string | null
  last_settled_date: string | null
  last_settlement_utr: string | null
  last_settlement_status: string | null
  history: CashfreeSettlementItem[]
}

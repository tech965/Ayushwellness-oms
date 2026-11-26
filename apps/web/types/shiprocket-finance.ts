import type { DrillTotals, UploadPreview } from "@/types/remittance"

export type FinanceKind = "expenses" | "remittances"

export interface FinanceCard {
  key: string
  label: string
  definition: string
  count: number
  amount: string
}

export interface FinanceSource {
  data: string
  method: "api" | "report" | "not_available" | "not_ingested"
  endpoint: string | null
  state:
    | "ready"
    | "not_configured"
    | "not_enabled"
    | "report_only"
    | "not_available"
    | "not_ingested"
  note: string
}

export interface FinanceFilters {
  date_from?: string
  date_to?: string
  q?: string
  courier?: string
  txn_type?: string
  status?: string
  bank_state?: string
}

export interface ExpenseSummary {
  cards: FinanceCard[]
  transaction_count: number
  couriers: string[]
  wallet: { balance: string | null; stale: boolean; ledger_sync_enabled: boolean }
}

export interface PayoutSummary {
  cards: FinanceCard[]
  payout_count: number
  statuses: string[]
  bank_data_available: boolean
}

export interface ExpenseRow {
  id: string
  transaction_ref: string | null
  txn_date: string
  txn_type: string
  direction: string
  amount: string
  order_number: string | null
  awb: string | null
  courier_name: string | null
  description: string | null
  status: string
  source: string
  contribution: string
}

export interface PayoutRow {
  id: string
  crf_id: string | null
  utr: string | null
  payout_date: string | null
  status: string | null
  cod_amount: string | null
  freight_deduction: string | null
  other_deductions: string | null
  net_amount: string
  unexplained_difference: string | null
  bank_amount: string | null
  bank_state: string
  bank_state_label: string
  contribution: string
}

export interface ChargeRow {
  awb: string
  order_number: string | null
  zone: string | null
  freight_charges: string | null
  cod_charges: string | null
  applied_weight: string | null
  charged_weight: string | null
  fetched_at: string
}

export interface PayoutDetail {
  payout: PayoutRow
  bank_credits: {
    reference: string
    amount: string
    credit_date: string | null
    narration: string | null
  }[]
  awb_lines: {
    awb: string | null
    order_ref: string | null
    reference: string | null
    amount: string
    matched_to_order: boolean
  }[]
  awb_lines_total: string
}

export interface OrderFinanceSyncResult {
  status: "ok" | "partial" | "access_denied"
  candidates: number
  checked: number
  with_charges: number
  with_remittance_reference: number
  unreadable: number
  failed: number
  message: string | null
}

export interface FinanceImportResult {
  added: number
  updated: number
  duplicates_skipped: number
  conflicts: number
  rejected: number
  rows_read: number
  row_errors: { row: number; error: string }[]
  conflict_rows: { payout: string | null; differs_in: string[] }[]
}

export type { DrillTotals, UploadPreview }

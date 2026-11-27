export type RemittanceModule = "cod" | "prepaid"

export type RemittanceStatus =
  | "pending"
  | "partially_reconciled"
  | "matched"
  | "short"
  | "excess"
  | "failed"
  | "refunded"

/** Money arrives as a decimal string (never a float) so nothing is rounded in transit. */
export interface KpiAmount {
  count: number
  amount: string
  [extra: string]: number | string
}

export interface PercentageItem {
  key: string
  label: string
  kind: "count" | "value"
  numerator: string
  denominator: string
  numerator_label: string
  denominator_label: string
  value: number | null
  /** The KPI selector to open when this percentage is clicked (or "unmatched"). */
  drill: string | null
}

export interface StatusCount {
  status: RemittanceStatus
  label: string
  count: number
  amount: string
  percent_of_orders: number | null
}

/** Which remittance / bank evidence OMS holds at all (COD summary only). */
export interface RemittanceDataCoverage {
  has_remittance_data: boolean
  has_bank_data: boolean
  /** True when nothing is loaded, so "Payment Pending" means "no data", not "unpaid". */
  pending_is_data_limited: boolean
  api_sync_configured: boolean
  remittance_lines: number
  api_reported_remittances: number
  /** Shiprocket order-detail payout reference/date with no amount (counts as Initiated). */
  order_payout_references?: number
  bank_credits: number
  manual_bank_verifications: number
  last_upload_at: string | null
  last_api_sync_at: string | null
  /** Payment Pending orders in the period, and how many were never read from Shiprocket. */
  pending_orders?: number
  pending_unchecked_orders?: number | null
  /** false: the never-checked count cannot be determined (database update pending). */
  pending_check_available?: boolean
}

export interface RemittanceSummary {
  kpis: Record<string, KpiAmount>
  percentages: PercentageItem[]
  status_breakdown: StatusCount[]
  partners?: PartnerRow[] | null
  gateways?: GatewayRow[] | null
  denominators: Record<string, number | string>
  data_coverage?: RemittanceDataCoverage | null
}

export interface PartnerRow {
  partner: string
  delivered_orders: number
  expected_amount: string
  remittance_amount: string
  amount_received: string
  pending_orders: number
  short_orders: number
  excess_orders: number
  matched_orders: number
  unmatched_lines: number
  completion_percent: number | null
}

export interface GatewayRow {
  gateway: string
  orders: number
  gross_amount: string
  gateway_charges: string
  gst_amount: string
  refund_amount: string
  expected_net: string
  reported_settlement: string
  bank_settlement: string
  pending_orders: number
  matched_orders: number
  unmatched_lines: number
  completion_percent: number | null
}

export interface DrillTotals {
  measure: string
  count: number
  amount: string
  /** Same selection without the search term: what the KPI card shows. */
  base_count: number
  base_amount: string
  breakdown: { label: string; count: number; amount: string }[]
}

export interface DetailLine {
  id: string
  source: string
  partner?: string | null
  gateway?: string | null
  awb?: string | null
  event_type?: string | null
  transaction_id?: string | null
  order_ref: string | null
  settlement_id?: string | null
  reference: string | null
  gross_amount?: string | null
  gateway_charges?: string | null
  gst_amount?: string | null
  refund_amount?: string | null
  amount: string
  date: string | null
  line_status?: string | null
  counted?: boolean
  verification?: "successful" | "unsuccessful" | "unverified"
  verification_label?: string
  match_method: string | null
}

export interface ReconciliationDetail {
  module: RemittanceModule
  record: CodRecord | PrepaidRecord
  lines: DetailLine[]
  payments?: {
    provider: string | null
    transaction_id: string | null
    status: string | null
    amount: string
    paid_at: string | null
  }[]
  refunds?: { status: string | null; amount: string }[]
  bank_links: BankEvidenceRow[]
  bank_references?: ReferencePosition[]
  adjustments: { amount: string; reason: string; created_at: string; reversed: boolean }[]
}

export type BankConfirmationStatus =
  "not_applicable" | "not_confirmed" | "partially_confirmed" | "confirmed"

/** Bank confirmation and adjustment effect, per order. */
export interface BankPosition {
  bank_expected: string
  bank_statement_amount: string
  bank_manual_amount: string
  bank_remaining: string
  bank_over: string
  bank_status: BankConfirmationStatus
  bank_status_label: string
  bank_evidence?: string | null
}

export interface AdjustedPosition extends BankPosition {
  difference_before_adjustments: string
  adjustment_effect: string
}

export interface ReferencePosition extends BankPosition {
  reference: string
}

export interface BankEvidenceRow {
  id: string
  reference: string
  linked_amount: string
  method: string
  verification: "statement_matched" | "manually_verified"
  verification_label: string
  bank_reference: string | null
  reason: string | null
  credit_amount: string
  credit_date: string | null
  narration: string | null
}

export interface CodRecord extends AdjustedPosition {
  order_id: string
  order_amount?: string
  payment_status?: string | null
  delivery_status?: string | null
  contribution?: string
  order_number: string
  awb: string | null
  customer_name: string | null
  partner: string
  service_name: string | null
  order_datetime: string
  delivery_date: string | null
  expected_amount: string
  remittance_amount: string
  amount_received: string
  adjustment_amount: string
  difference: string
  remittance_reference: string | null
  remittance_date: string | null
  bank_credit_date: string | null
  status: RemittanceStatus
  status_label: string
  source: string | null
  age_days: number
  line_count: number
}

export interface PrepaidRecord extends AdjustedPosition {
  unverified_line_count?: number
  unverified_amount?: string
  unverified_statuses?: string | null
  order_id: string
  cashfree_payment_id?: string | null
  settlement_id?: string | null
  payment_status?: string | null
  delivery_status?: string | null
  contribution?: string
  order_number: string
  customer_name: string | null
  gateway: string
  gateway_transaction_id: string | null
  transaction_date: string | null
  order_datetime: string
  order_amount: string
  gross_amount: string
  gateway_charges: string
  gst_amount: string
  refund_amount: string
  expected_net: string
  actual_settlement: string
  bank_confirmed_settlement: string
  adjustment_amount: string
  bank_reference: string | null
  settlement_date: string | null
  bank_credit_date: string | null
  difference: string
  settlement_gap: string
  status: RemittanceStatus
  status_label: string
  payment_recorded: boolean
  line_count: number
  age_days: number
}

export interface UnmatchedRow {
  kind: "remittance_line" | "gateway_line" | "bank_credit"
  id: string
  contribution?: string
  settlement_id?: string | null
  cashfree_payment_id?: string | null
  line_status?: string | null
  verification?: "successful" | "unsuccessful" | "unverified" | null
  gross_amount?: string | null
  gateway_charges?: string | null
  gst_amount?: string | null
  refund_amount?: string | null
  partner?: string | null
  gateway?: string | null
  awb?: string | null
  transaction_id?: string | null
  order_ref?: string | null
  reference: string | null
  amount: string
  date: string | null
  reason: string
  matched_order_id?: string | null
}

export interface BankCreditRow {
  id: string
  credit_date: string | null
  amount: string
  reference: string | null
  narration: string | null
  linked_amount: string
  unlinked_amount: string
  linked_references: string | null
  statement_matched_amount: string
  manually_verified_amount: string
  links?: BankEvidenceRow[]
  link_state: "linked" | "partially_linked" | "unlinked"
}

export interface SettlementFileRow {
  id: string
  module: string
  source_name: string
  filename: string
  size_bytes: number
  row_count: number
  accepted_count: number
  duplicate_count: number
  rejected_count: number
  created_at: string
}

export interface UploadResult {
  file_id: string
  filename: string
  rows_read: number
  accepted: number
  duplicates_skipped?: number | null
  duplicates_or_non_credits_skipped?: number | null
  rejected: number
  auto_matched_to_orders?: number | null
  unmatched?: number | null
  bank_credits_linked?: number | null
  unverified_rows?: number | null
  unsuccessful_rows?: number | null
  statuses_updated?: number | null
  auto_linked_cod?: number | null
  auto_linked_prepaid?: number | null
  row_errors: { row: number; error: string }[]
  /** field -> header the server actually read it from */
  column_mapping?: Record<string, string>
  ignored_columns?: string[]
}

export interface SourceStatus {
  key: string
  label: string
  api_supported: boolean
  api_configured: boolean
  upload_supported: boolean
  last_synced_at: string | null
  note: string
  connection_status?: CashfreeConnectionStatus | null
}

export type CashfreeConnectionStatus =
  "not_configured" | "never_synced" | "connected" | "degraded" | "error"

export interface CashfreeSyncStatus {
  gateway: string
  connection_status: CashfreeConnectionStatus
  connection_message: string
  configured: boolean
  environment: string | null
  sync_enabled: boolean
  sync_interval_minutes: number
  last_attempt_at: string | null
  last_successful_sync_at: string | null
  last_sync_status: string | null
  last_sync_error: string | null
  last_sync_trigger: string | null
  synced_through: string | null
  last_webhook_at: string | null
  running: boolean
  /** A manual sync is waiting for the background worker. */
  queued?: boolean
  queued_at?: string | null
  settlement_lots: number
  settlement_lots_by_status: Record<string, number>
  synced_lines: number
  bank_confirmation_note: string
}

export interface CashfreeSyncStart {
  status: string
  message?: string
}

export interface RemittanceFilters {
  date_from?: string
  date_to?: string
  /** COD: shipping partner. Prepaid: payment gateway. */
  partner?: string
  gateway?: string
  status?: string
  q?: string
}

export interface SettlementLot {
  gateway: string
  settlement_id: string
  utr: string | null
  status: string | null
  processed_on: string | null
  payment_amount: string
  charges: string
  gst: string
  amount_settled: string
  bank_credited: string
  bank_state: "linked" | "partially_linked" | "unlinked"
}

export interface UploadPreview {
  headers: string[]
  /** field -> the header the server would use */
  suggested_mapping: Record<string, string>
  fields: { field: string; label: string; template_header: string }[]
  /** each inner list needs at least one mapped field */
  required_groups: string[][]
  missing: string[]
  ambiguous: Record<string, string[]>
  ignored: string[]
  sample_rows: string[][]
  can_import: boolean
  problems: string[]
}

export type UploadKind = "report" | "bank"

/** What matching one COD remittance line to one order would do (server-computed). */
export interface MatchPreview {
  order_id: string
  order_number: string
  awb: string | null
  customer_name: string | null
  expected_amount: string
  line_amount: string
  other_remitted: string
  adjustment_amount: string
  projected_total: string
  projected_difference: string
  projected_status: RemittanceStatus
  projected_status_label: string
  blockers: string[]
  warnings: string[]
  needs_acknowledgement: boolean
}

export interface MatchLookup {
  found: boolean
  preview: MatchPreview | null
}

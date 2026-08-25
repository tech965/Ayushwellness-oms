// Mirrors app/schemas/reconciliation.py

export type ReconciliationRunStatus = "running" | "completed" | "failed"
export type ReconciliationResultStatus = "reconciled" | "mismatch" | "missing" | "error"

export interface ReconciliationRun {
  id: string
  triggered_by_user_id: string | null
  status: ReconciliationRunStatus
  started_at: string | null
  completed_at: string | null
  total_checked: number
  reconciled_count: number
  mismatch_count: number
  missing_count: number
  error_count: number
  run_metadata: { skipped_checks?: string[]; errored_checks?: string[] } | null
  created_at: string
}

export interface ReconciliationResult {
  id: string
  run_id: string
  check_type: string
  provider: string
  entity_type: string
  internal_id: string | null
  external_id: string | null
  expected_value: Record<string, unknown> | null
  actual_value: Record<string, unknown> | null
  status: ReconciliationResultStatus
  message: string | null
  resolved: boolean
  resolved_at: string | null
  resolved_by_user_id: string | null
  created_at: string
}

export interface ReconciliationResultListFilters {
  run_id?: string
  status?: ReconciliationResultStatus
  check_type?: string
  provider?: string
  resolved?: boolean
}

export const RECONCILIATION_RESULT_STATUS_OPTIONS: {
  label: string
  value: ReconciliationResultStatus
}[] = [
  { label: "Reconciled", value: "reconciled" },
  { label: "Mismatch", value: "mismatch" },
  { label: "Missing", value: "missing" },
  { label: "Error", value: "error" },
]

import {
  keepPreviousData,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query"

import { apiClient } from "@/lib/api-client"
import type { ApiResponse, PaginatedResponse } from "@/types/api"
import type {
  BankCreditRow,
  CashfreeSyncStart,
  CashfreeSyncStatus,
  CodRecord,
  CrfTransactionRow,
  DrillTotals,
  ReconciliationDetail,
  MatchLookup,
  PrepaidRecord,
  RemittanceFilters,
  RemittanceModule,
  RemittanceSummary,
  SettlementFileRow,
  SettlementLot,
  SourceStatus,
  UnmatchedRow,
  UploadPreview,
  UploadResult,
} from "@/types/remittance"

const ROOT = "/remittance-reconciliation"

function clean<T extends object>(params: T): Record<string, unknown> {
  return Object.fromEntries(
    Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== "")
  )
}

/** Keeps the previous page only while paging/searching inside the SAME
 * selection and filters, so rows from another card/filter never show under a
 * new title (same rule as the shipment dashboards).
 */
function keepWithinScope<T>(scope: unknown) {
  return (previous: T | undefined, previousQuery?: { queryKey: readonly unknown[] }) =>
    previousQuery &&
    JSON.stringify(previousQuery.queryKey.at(-2)) === JSON.stringify(scope)
      ? previous
      : undefined
}

export function useRemittanceSummary(
  module: RemittanceModule,
  filters: RemittanceFilters
) {
  return useQuery({
    queryKey: ["remittance", module, "summary", filters],
    queryFn: async (): Promise<RemittanceSummary> => {
      const response = await apiClient.get<ApiResponse<RemittanceSummary>>(
        `${ROOT}/${module}/summary`,
        { params: clean(filters) }
      )
      if (!response.data.data) throw new Error("Reconciliation summary not found.")
      return response.data.data
    },
  })
}

export type DrilldownPage<T> = PaginatedResponse<T> & { totals?: DrillTotals }

interface RecordsParams {
  module: RemittanceModule
  kpi?: string
  ageBucket?: string
  settledOn?: string
  filters: RemittanceFilters
  page: number
  pageSize: number
  enabled?: boolean
}

export function useRemittanceRecords<T extends CodRecord | PrepaidRecord>(
  params: RecordsParams
) {
  const {
    module,
    kpi,
    ageBucket,
    settledOn,
    filters,
    page,
    pageSize,
    enabled = true,
  } = params
  const scope = {
    module,
    kpi,
    ageBucket,
    settledOn,
    filters: { ...filters, q: undefined },
  }
  return useQuery({
    queryKey: ["remittance", module, "records", scope, { q: filters.q, page, pageSize }],
    queryFn: async (): Promise<DrilldownPage<T>> => {
      const response = await apiClient.get<DrilldownPage<T>>(
        `${ROOT}/${module}/records`,
        {
          params: clean({
            ...filters,
            kpi,
            age_bucket: ageBucket,
            settled_on: settledOn,
            page,
            page_size: pageSize,
          }),
        }
      )
      return response.data
    },
    enabled,
    placeholderData: keepWithinScope<DrilldownPage<T>>(scope),
  })
}

/** The transaction-detail list behind the 3 CRF amount KPIs (COD only).
 * Every row is a real, synced `CodRemittanceLine`/`CodRemittance` record --
 * an empty page means no CRF data has been synced in this environment yet,
 * never a placeholder. */
export function useCrfTransactions(params: {
  status?: "success" | "initiated" | "pending"
  page: number
  pageSize: number
  enabled?: boolean
}) {
  const { status, page, pageSize, enabled = true } = params
  return useQuery({
    queryKey: ["remittance", "cod", "crf-transactions", status, page, pageSize],
    queryFn: async (): Promise<PaginatedResponse<CrfTransactionRow>> => {
      const response = await apiClient.get<PaginatedResponse<CrfTransactionRow>>(
        `${ROOT}/cod/crf-transactions`,
        { params: clean({ status, page, page_size: pageSize }) }
      )
      return response.data
    },
    enabled,
    placeholderData: keepWithinScope<PaginatedResponse<CrfTransactionRow>>(status),
  })
}

export function useRemittanceUnmatched(
  module: RemittanceModule,
  filters: RemittanceFilters,
  kind: string | undefined,
  page: number,
  pageSize: number,
  enabled = true
) {
  const scope = { module, kind, filters: { ...filters, q: undefined } }
  return useQuery({
    queryKey: [
      "remittance",
      module,
      "unmatched",
      scope,
      { q: filters.q, page, pageSize },
    ],
    queryFn: async (): Promise<DrilldownPage<UnmatchedRow>> => {
      const response = await apiClient.get<DrilldownPage<UnmatchedRow>>(
        `${ROOT}/${module}/unmatched`,
        { params: clean({ ...filters, kind, page, page_size: pageSize }) }
      )
      return response.data
    },
    enabled,
    placeholderData: keepWithinScope<DrilldownPage<UnmatchedRow>>(scope),
  })
}

export function useRemittanceReport<T = Record<string, unknown>>(
  module: RemittanceModule,
  report: string,
  filters: RemittanceFilters,
  enabled = true
) {
  return useQuery({
    queryKey: ["remittance", module, "report", report, filters],
    queryFn: async (): Promise<T[]> => {
      const response = await apiClient.get<ApiResponse<T[]>>(
        `${ROOT}/${module}/reports/${report}`,
        { params: clean(filters) }
      )
      return response.data.data ?? []
    },
    enabled,
  })
}

export function useBankCredits(
  module: RemittanceModule,
  filters: RemittanceFilters,
  page: number,
  pageSize: number,
  enabled = true
) {
  return useQuery({
    queryKey: ["remittance", module, "bank-credits", filters, page, pageSize],
    queryFn: async (): Promise<PaginatedResponse<BankCreditRow>> => {
      const response = await apiClient.get<PaginatedResponse<BankCreditRow>>(
        `${ROOT}/${module}/bank-credits`,
        { params: clean({ ...filters, page, page_size: pageSize }) }
      )
      return response.data
    },
    enabled,
    placeholderData: keepPreviousData,
  })
}

export function useSettlementLots(
  filters: RemittanceFilters,
  page: number,
  pageSize: number,
  enabled = true
) {
  return useQuery({
    queryKey: ["remittance", "prepaid", "lots", filters, page, pageSize],
    queryFn: async (): Promise<PaginatedResponse<SettlementLot>> => {
      const response = await apiClient.get<PaginatedResponse<SettlementLot>>(
        `${ROOT}/prepaid/settlement-lots`,
        { params: clean({ ...filters, page, page_size: pageSize }) }
      )
      return response.data
    },
    enabled,
    placeholderData: keepPreviousData,
  })
}

export function useRemittanceUploads(module: RemittanceModule, enabled = true) {
  return useQuery({
    queryKey: ["remittance", module, "uploads"],
    queryFn: async (): Promise<SettlementFileRow[]> => {
      const response = await apiClient.get<ApiResponse<SettlementFileRow[]>>(
        `${ROOT}/${module}/uploads`
      )
      return response.data.data ?? []
    },
    enabled,
  })
}

export function useRemittanceSources() {
  return useQuery({
    queryKey: ["remittance", "sources"],
    queryFn: async (): Promise<{ cod: SourceStatus[]; prepaid: SourceStatus[] }> => {
      const response = await apiClient.get<
        ApiResponse<{ cod: SourceStatus[]; prepaid: SourceStatus[] }>
      >(`${ROOT}/sources`)
      if (!response.data.data) throw new Error("Sources not found.")
      return response.data.data
    },
  })
}

/** Cashfree connection status + last successful sync. Read-only (no Cashfree
 * call, no credentials). Polls while a sync is running, or after one was
 * started (`pollSince` = the `last_attempt_at` seen at that moment, `null`
 * meaning "no attempt yet") until a newer attempt appears.
 */
export function useCashfreeSyncStatus(pollSince?: string | null) {
  return useQuery({
    queryKey: ["remittance", "prepaid", "cashfree-status"],
    queryFn: async (): Promise<CashfreeSyncStatus> => {
      const response = await apiClient.get<ApiResponse<CashfreeSyncStatus>>(
        `${ROOT}/prepaid/cashfree/status`
      )
      if (!response.data.data) throw new Error("Cashfree status not found.")
      return response.data.data
    },
    refetchInterval: (query) => {
      const current = query.state.data
      if (current?.running || current?.queued) return 2500
      if (pollSince === undefined) return false
      return current && current.last_attempt_at !== pollSince ? false : 2500
    },
  })
}

function useInvalidate() {
  const queryClient = useQueryClient()
  return () => queryClient.invalidateQueries({ queryKey: ["remittance"] })
}

export function useUploadRemittanceReport(module: RemittanceModule) {
  const invalidate = useInvalidate()
  return useMutation({
    mutationFn: async (input: {
      name: string
      file: File
      columnMap?: Record<string, string>
    }): Promise<UploadResult> => {
      const form = new FormData()
      form.append(module === "cod" ? "partner" : "gateway", input.name)
      if (input.columnMap) form.append("column_map", JSON.stringify(input.columnMap))
      form.append("file", input.file)
      const response = await apiClient.post<ApiResponse<UploadResult>>(
        `${ROOT}/${module}/uploads`,
        form,
        { timeout: 60_000 }
      )
      if (!response.data.data) throw new Error("Upload failed.")
      return response.data.data
    },
    onSuccess: () => void invalidate(),
  })
}

export function useUploadBankStatement() {
  const invalidate = useInvalidate()
  return useMutation({
    mutationFn: async (input: {
      name: string
      file: File
      columnMap?: Record<string, string>
    }): Promise<UploadResult> => {
      const form = new FormData()
      form.append("source_name", input.name)
      if (input.columnMap) form.append("column_map", JSON.stringify(input.columnMap))
      form.append("file", input.file)
      const response = await apiClient.post<ApiResponse<UploadResult>>(
        `${ROOT}/bank-statements`,
        form,
        { timeout: 60_000 }
      )
      if (!response.data.data) throw new Error("Upload failed.")
      return response.data.data
    },
    onSuccess: () => void invalidate(),
  })
}

export function useRematch(module: RemittanceModule) {
  const invalidate = useInvalidate()
  return useMutation({
    mutationFn: async () => {
      const response = await apiClient.post<ApiResponse<Record<string, number>>>(
        `${ROOT}/${module}/rematch`
      )
      return response.data.data ?? {}
    },
    onSuccess: () => void invalidate(),
  })
}

export function useAddAdjustment(module: RemittanceModule) {
  const invalidate = useInvalidate()
  return useMutation({
    mutationFn: async (input: { order_id: string; amount: string; reason: string }) => {
      await apiClient.post(`${ROOT}/${module}/adjustments`, input)
    },
    onSuccess: () => void invalidate(),
  })
}

export function useLinkBankCredit(module: RemittanceModule) {
  const invalidate = useInvalidate()
  return useMutation({
    mutationFn: async (input: {
      bank_credit_id: string
      reference: string
      bank_reference: string
      amount: string
      reason: string
    }) => {
      await apiClient.post(`${ROOT}/${module}/bank-links`, clean(input))
    },
    onSuccess: () => void invalidate(),
  })
}

export function useMatchLine(module: RemittanceModule) {
  const invalidate = useInvalidate()
  return useMutation({
    mutationFn: async (input: { line_id: string; order_id: string }) => {
      await apiClient.post(`${ROOT}/${module}/lines/${input.line_id}/match`, {
        order_id: input.order_id,
      })
    },
    onSuccess: () => void invalidate(),
  })
}

/** Downloads the Excel workbook for the current filters. */
export async function downloadRemittanceExport(
  module: RemittanceModule,
  filters: RemittanceFilters
): Promise<void> {
  const response = await apiClient.get(`${ROOT}/${module}/export`, {
    params: clean(filters),
    responseType: "blob",
    timeout: 60_000,
  })
  const url = window.URL.createObjectURL(new Blob([response.data]))
  const link = document.createElement("a")
  link.href = url
  link.download =
    module === "cod"
      ? "cod-remittance-reconciliation.xlsx"
      : "prepaid-remittance-reconciliation.xlsx"
  document.body.appendChild(link)
  link.click()
  link.remove()
  window.URL.revokeObjectURL(url)
}

export async function downloadOriginalFile(
  fileId: string,
  filename: string
): Promise<void> {
  const response = await apiClient.get(`${ROOT}/files/${fileId}/download`, {
    responseType: "blob",
  })
  const url = window.URL.createObjectURL(new Blob([response.data]))
  const link = document.createElement("a")
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  window.URL.revokeObjectURL(url)
}

/** Reads a file's columns on the server WITHOUT importing anything. */
export function usePreviewUpload(module: RemittanceModule, kind: "report" | "bank") {
  return useMutation({
    mutationFn: async (file: File): Promise<UploadPreview> => {
      const form = new FormData()
      form.append("file", file)
      const path =
        kind === "bank" ? "/bank-statements/preview" : `/${module}/uploads/preview`
      const response = await apiClient.post<ApiResponse<UploadPreview>>(
        `${ROOT}${path}`,
        form,
        {
          timeout: 60_000,
        }
      )
      if (!response.data.data) throw new Error("Preview failed.")
      return response.data.data
    },
  })
}

export async function downloadUploadTemplate(
  module: RemittanceModule,
  kind: "report" | "bank"
): Promise<void> {
  const path =
    kind === "bank" ? "/bank-statements/template" : `/${module}/uploads/template`
  const response = await apiClient.get(`${ROOT}${path}`, { responseType: "blob" })
  const url = window.URL.createObjectURL(new Blob([response.data]))
  const link = document.createElement("a")
  link.href = url
  link.download =
    kind === "bank" ? "bank-statement-template.csv" : `${module}-template.csv`
  document.body.appendChild(link)
  link.click()
  link.remove()
  window.URL.revokeObjectURL(url)
}

/** Exact order id / order number / AWB -> a server-computed preview of the match. */
export function useLookupOrderForLine() {
  return useMutation({
    mutationFn: async (input: { line_id: string; q: string }): Promise<MatchLookup> => {
      const response = await apiClient.get<ApiResponse<MatchLookup>>(
        `${ROOT}/cod/lines/${input.line_id}/lookup`,
        { params: { q: input.q } }
      )
      if (!response.data.data) throw new Error("Lookup failed.")
      return response.data.data
    },
  })
}

export function useConfirmedMatch() {
  const invalidate = useInvalidate()
  return useMutation({
    mutationFn: async (input: {
      line_id: string
      order_id: string
      acknowledge_difference: boolean
    }) => {
      await apiClient.post(`${ROOT}/cod/lines/${input.line_id}/match`, {
        order_id: input.order_id,
        confirm: true,
        acknowledge_difference: input.acknowledge_difference,
      })
    },
    onSuccess: () => void invalidate(),
  })
}

export function useCashfreeSyncNow() {
  const invalidate = useInvalidate()
  return useMutation({
    mutationFn: async (): Promise<CashfreeSyncStart> => {
      const response = await apiClient.post<ApiResponse<CashfreeSyncStart>>(
        `${ROOT}/prepaid/cashfree/sync`
      )
      if (!response.data.data) throw new Error("Sync could not be started.")
      return response.data.data
    },
    onSuccess: () => void invalidate(),
  })
}

/** One order's reconciliation record and evidence (read-only). */
export function useReconciliationDetail(module: RemittanceModule, orderId: string) {
  return useQuery({
    queryKey: ["remittance", module, "detail", orderId],
    queryFn: async (): Promise<ReconciliationDetail> => {
      const response = await apiClient.get<ApiResponse<ReconciliationDetail>>(
        `${ROOT}/${module}/orders/${orderId}/detail`
      )
      if (!response.data.data) throw new Error("Reconciliation details not found.")
      return response.data.data
    },
  })
}

export interface DrilldownExportParams {
  view: "records" | "unmatched"
  filters: RemittanceFilters
  kpi?: string
  kind?: string
  ageBucket?: string
  settledOn?: string
}

/** Excel of exactly the records in the open drilldown (all pages). */
export async function downloadDrilldownExport(
  module: RemittanceModule,
  { view, filters, kpi, kind, ageBucket, settledOn }: DrilldownExportParams
) {
  const response = await apiClient.get<Blob>(`${ROOT}/${module}/drilldown/export`, {
    params: clean({
      ...filters,
      view,
      kpi,
      kind,
      age_bucket: ageBucket,
      settled_on: settledOn,
    }),
    responseType: "blob",
    timeout: 60_000,
  })
  const url = URL.createObjectURL(response.data)
  const link = document.createElement("a")
  link.href = url
  link.download = `${module}-reconciliation-drilldown.xlsx`
  link.click()
  URL.revokeObjectURL(url)
}

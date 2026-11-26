import {
  keepPreviousData,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query"

import { apiClient } from "@/lib/api-client"
import type { ApiResponse, PaginatedResponse } from "@/types/api"
import type {
  ChargeRow,
  DrillTotals,
  ExpenseRow,
  ExpenseSummary,
  FinanceFilters,
  FinanceImportResult,
  FinanceKind,
  FinanceSource,
  OrderFinanceSyncResult,
  PayoutDetail,
  PayoutRow,
  PayoutSummary,
  UploadPreview,
} from "@/types/shiprocket-finance"

const ROOT = "/shiprocket-finance"

function clean<T extends object>(params: T): Record<string, unknown> {
  return Object.fromEntries(
    Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== "")
  )
}

export type DrillPage<T> = PaginatedResponse<T> & { totals: DrillTotals }

export function useFinanceSources(kind: FinanceKind) {
  return useQuery({
    queryKey: ["shiprocket-finance", kind, "sources"],
    queryFn: async (): Promise<FinanceSource[]> => {
      const response = await apiClient.get<ApiResponse<FinanceSource[]>>(
        `${ROOT}/${kind}/sources`
      )
      return response.data.data ?? []
    },
  })
}

export function useFinanceSummary<T extends ExpenseSummary | PayoutSummary>(
  kind: FinanceKind,
  filters: FinanceFilters
) {
  return useQuery({
    queryKey: ["shiprocket-finance", kind, "summary", filters],
    queryFn: async (): Promise<T> => {
      const response = await apiClient.get<ApiResponse<T>>(`${ROOT}/${kind}/summary`, {
        params: clean(filters),
      })
      if (!response.data.data) throw new Error("Summary not found.")
      return response.data.data
    },
  })
}

export function useFinanceRecords<T extends ExpenseRow | PayoutRow>(input: {
  kind: FinanceKind
  kpi: string | undefined
  filters: FinanceFilters
  page: number
  pageSize: number
}) {
  const { kind, kpi, filters, page, pageSize } = input
  return useQuery({
    queryKey: ["shiprocket-finance", kind, "records", kpi, filters, page, pageSize],
    queryFn: async (): Promise<DrillPage<T>> => {
      const response = await apiClient.get<DrillPage<T>>(`${ROOT}/${kind}/records`, {
        params: clean({ ...filters, kpi, page, page_size: pageSize }),
      })
      return response.data
    },
    enabled: Boolean(kpi),
    placeholderData: keepPreviousData,
  })
}

export function useShipmentCharges(q: string, page: number, enabled: boolean) {
  return useQuery({
    queryKey: ["shiprocket-finance", "charges", q, page],
    queryFn: async (): Promise<
      PaginatedResponse<ChargeRow> & { coverage: Record<string, number> }
    > => {
      const response = await apiClient.get(`${ROOT}/expenses/shipment-charges`, {
        params: clean({ q, page, page_size: 10 }),
      })
      return response.data
    },
    enabled,
    placeholderData: keepPreviousData,
  })
}

export function usePayoutDetail(id: string) {
  return useQuery({
    queryKey: ["shiprocket-finance", "remittances", "detail", id],
    queryFn: async (): Promise<PayoutDetail> => {
      const response = await apiClient.get<ApiResponse<PayoutDetail>>(
        `${ROOT}/remittances/${id}`
      )
      if (!response.data.data) throw new Error("Payout not found.")
      return response.data.data
    },
  })
}

function useInvalidate() {
  const queryClient = useQueryClient()
  return () => queryClient.invalidateQueries({ queryKey: ["shiprocket-finance"] })
}

export function useSyncOrderFinance() {
  const invalidate = useInvalidate()
  return useMutation({
    mutationFn: async (): Promise<OrderFinanceSyncResult> => {
      const response = await apiClient.post<ApiResponse<OrderFinanceSyncResult>>(
        `${ROOT}/expenses/sync-order-finance`,
        undefined,
        { params: { limit: 10 }, timeout: 120_000 }
      )
      if (!response.data.data) throw new Error("Sync failed.")
      return response.data.data
    },
    onSuccess: () => void invalidate(),
  })
}

export function usePreviewFinanceImport(kind: FinanceKind) {
  return useMutation({
    mutationFn: async (file: File): Promise<UploadPreview> => {
      const form = new FormData()
      form.append("file", file)
      const response = await apiClient.post<ApiResponse<UploadPreview>>(
        `${ROOT}/${kind}/import/preview`,
        form
      )
      if (!response.data.data) throw new Error("The file could not be read.")
      return response.data.data
    },
  })
}

export function useFinanceImport(kind: FinanceKind) {
  const invalidate = useInvalidate()
  return useMutation({
    mutationFn: async (input: {
      file: File
      columnMap: Record<string, string>
    }): Promise<FinanceImportResult> => {
      const form = new FormData()
      form.append("column_map", JSON.stringify(input.columnMap))
      form.append("file", input.file)
      const response = await apiClient.post<ApiResponse<FinanceImportResult>>(
        `${ROOT}/${kind}/import`,
        form,
        { timeout: 60_000 }
      )
      if (!response.data.data) throw new Error("Import failed.")
      return response.data.data
    },
    onSuccess: () => void invalidate(),
  })
}

export async function downloadFinanceExport(
  kind: FinanceKind,
  kpi: string,
  filters: FinanceFilters
) {
  const response = await apiClient.get<Blob>(`${ROOT}/${kind}/export`, {
    params: clean({ ...filters, kpi }),
    responseType: "blob",
    timeout: 60_000,
  })
  const url = URL.createObjectURL(response.data)
  const link = document.createElement("a")
  link.href = url
  link.download = `shiprocket-${kind}-${kpi}.xlsx`
  link.click()
  URL.revokeObjectURL(url)
}

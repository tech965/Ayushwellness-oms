import { useMutation, useQuery } from "@tanstack/react-query"

import { apiClient } from "@/lib/api-client"
import type { ApiResponse } from "@/types/api"
import type {
  AccountSalesReport,
  AmazonStatus,
  ChunkRef,
  DayOrdersReport,
  PollResponse,
  SalesReport,
} from "@/types/amazon-sales"

const BASE = "/marketplaces/amazon/sales"

export function useAmazonStatus() {
  return useQuery({
    queryKey: ["amazon-sales", "status"],
    queryFn: async () =>
      (await apiClient.get<ApiResponse<AmazonStatus>>(`${BASE}/status`)).data.data!,
  })
}

export async function startAmazonReports(
  date_from: string,
  date_to: string
): Promise<ChunkRef[]> {
  const r = await apiClient.post<ApiResponse<ChunkRef[]>>(`${BASE}/reports`, {
    date_from,
    date_to,
  })
  return r.data.data ?? []
}

/** Stateless polling: the server keeps nothing; the browser holds Amazon's
 * report ids and re-asks until every report is DONE. */
export function useAmazonReportPoll(chunks: ChunkRef[] | null) {
  return useQuery({
    queryKey: ["amazon-sales", "poll", chunks?.map((c) => c.report_id)],
    enabled: !!chunks,
    queryFn: async () =>
      (
        await apiClient.post<ApiResponse<PollResponse>>(`${BASE}/reports/poll`, {
          chunks,
        })
      ).data.data!,
    refetchInterval: (query) => (query.state.data?.state === "processing" ? 5000 : false),
    retry: false,
    refetchOnWindowFocus: false,
  })
}

/** Account-level sales (Sales API) -- synchronous, unlike the Reports API
 * report flow above, so this is a plain query with no polling. */
export function useAmazonAccountSales(date_from: string, date_to: string) {
  return useQuery({
    queryKey: ["amazon-sales", "account", date_from, date_to],
    queryFn: async () =>
      (
        await apiClient.get<ApiResponse<AccountSalesReport>>(`${BASE}/account`, {
          params: { date_from, date_to },
        })
      ).data.data!,
    retry: false,
  })
}

/** Product-wise sales from the Orders API -- an alternative to the
 * Reports-API-backed flow above while that one returns 403. Synchronous
 * (no report-generation delay), so this is a plain query. One backend
 * call per order past the first 30 (Amazon's getOrderItems burst), at
 * 0.5 req/s -- a busy week can take minutes, well past the client's
 * default 15s timeout, so this request gets its own long timeout. */
export function useAmazonOrdersProductReport(date_from: string, date_to: string) {
  return useQuery({
    queryKey: ["amazon-sales", "orders-product-report", date_from, date_to],
    queryFn: async () =>
      (
        await apiClient.get<ApiResponse<SalesReport>>(`${BASE}/orders-product-report`, {
          params: { date_from, date_to },
          timeout: 300_000,
        })
      ).data.data!,
    retry: false,
    enabled: false,
  })
}

/** Order-level drill-down for one day -- product + COD/Prepaid only.
 * Lazy: only fetches when a date is actually selected (dialog opened). */
export function useAmazonOrdersByDate(date: string | null) {
  return useQuery({
    queryKey: ["amazon-sales", "orders-by-date", date],
    enabled: !!date,
    queryFn: async () =>
      (
        await apiClient.get<ApiResponse<DayOrdersReport>>(`${BASE}/orders-by-date`, {
          params: { date },
        })
      ).data.data!,
    retry: false,
  })
}

export function useAmazonExport() {
  return useMutation({
    mutationFn: async (chunks: ChunkRef[]) => {
      const response = await apiClient.post(
        `${BASE}/reports/export`,
        { chunks },
        { responseType: "blob" }
      )
      const url = window.URL.createObjectURL(new Blob([response.data]))
      const link = document.createElement("a")
      link.href = url
      link.download = "amazon-sales.xlsx"
      document.body.appendChild(link)
      link.click()
      link.remove()
      window.URL.revokeObjectURL(url)
    },
  })
}

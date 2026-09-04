import { useMutation, useQuery } from "@tanstack/react-query"

import { apiClient } from "@/lib/api-client"
import type { ApiResponse, PaginatedResponse } from "@/types/api"
import type { AnalyticsDateRangeParams, TimeseriesInterval } from "@/types/analytics"
import type {
  CashfreePaymentMethodBreakdown,
  CashfreePaymentOverview,
  CashfreePaymentTrend,
} from "@/types/cashfree"
import type { Payment, PaymentDetail, PaymentListFilters } from "@/types/payment"

interface ListParams extends PaymentListFilters {
  page: number
  pageSize: number
  sortBy?: string
  sortOrder?: "asc" | "desc"
}

function toPaymentQueryParams(params: PaymentListFilters) {
  return {
    order_id: params.order_id,
    provider: params.provider,
    status: params.status,
    payment_method: params.payment_method,
    q: params.q || undefined,
    date_from: params.date_from,
    date_to: params.date_to,
  }
}

async function fetchPaymentsForOrder(orderId: string): Promise<Payment[]> {
  const response = await apiClient.get<PaginatedResponse<Payment>>("/payments", {
    params: { order_id: orderId, page_size: 50 },
  })
  return response.data.data
}

export function usePaymentsForOrder(orderId: string) {
  return useQuery({
    queryKey: ["payments", "order", orderId],
    queryFn: () => fetchPaymentsForOrder(orderId),
    enabled: Boolean(orderId),
  })
}

async function fetchPayments(params: ListParams): Promise<PaginatedResponse<Payment>> {
  const response = await apiClient.get<PaginatedResponse<Payment>>("/payments", {
    params: {
      page: params.page,
      page_size: params.pageSize,
      sort_by: params.sortBy,
      sort_order: params.sortOrder,
      ...toPaymentQueryParams(params),
    },
  })
  return response.data
}

/** Backs the `/payments` dashboard table — every provider (Cashfree
 * today, a plain COD/manual payment too), filtered/paginated/sorted.
 */
export function usePayments(params: ListParams) {
  return useQuery({
    queryKey: ["payments", "list", params],
    queryFn: () => fetchPayments(params),
    placeholderData: (previous) => previous,
  })
}

async function fetchPayment(id: string): Promise<PaymentDetail> {
  const response = await apiClient.get<ApiResponse<PaymentDetail>>(`/payments/${id}`)
  if (!response.data.data) throw new Error("Payment not found.")
  return response.data.data
}

export function usePayment(id: string) {
  return useQuery({
    queryKey: ["payments", "detail", id],
    queryFn: () => fetchPayment(id),
    enabled: Boolean(id),
  })
}

async function downloadPaymentsExport(filters: PaymentListFilters): Promise<void> {
  const response = await apiClient.get("/payments/export", {
    params: toPaymentQueryParams(filters),
    responseType: "blob",
  })
  const url = window.URL.createObjectURL(new Blob([response.data]))
  const link = document.createElement("a")
  link.href = url
  link.download = "payments-export.xlsx"
  document.body.appendChild(link)
  link.click()
  link.remove()
  window.URL.revokeObjectURL(url)
}

/** Downloads the same filtered set as `usePayments`, unpaginated, as a
 * real `.xlsx` workbook (`GET /payments/export`) — mirrors
 * `useExportOrders`.
 */
export function useExportPayments() {
  return useMutation({
    mutationFn: downloadPaymentsExport,
  })
}

// --- Payment analytics (dashboard cards/charts) --------------------------
// Provider-agnostic sibling of `services/cashfree.ts`'s own
// `useCashfreePayment*` analytics hooks (untouched, still Cashfree-only).
// `provider` omitted/undefined means every provider, matching exactly
// what `usePayments`/the payments table already treat an absent provider
// filter as — so the dashboard cards above the table and the table can
// never silently disagree about what "no provider selected" shows.

interface AnalyticsParams extends AnalyticsDateRangeParams {
  provider?: string
}

async function fetchPaymentOverview(params: AnalyticsParams): Promise<CashfreePaymentOverview> {
  const response = await apiClient.get<ApiResponse<CashfreePaymentOverview>>(
    "/payments/analytics/overview",
    { params }
  )
  if (!response.data.data) throw new Error("Payment overview not available.")
  return response.data.data
}

export function usePaymentOverview(params: AnalyticsParams) {
  return useQuery({
    queryKey: ["payments", "analytics-overview", params],
    queryFn: () => fetchPaymentOverview(params),
    placeholderData: (previous) => previous,
  })
}

async function fetchPaymentTrend(
  params: AnalyticsParams & { interval: TimeseriesInterval }
): Promise<CashfreePaymentTrend> {
  const response = await apiClient.get<ApiResponse<CashfreePaymentTrend>>(
    "/payments/analytics/trend",
    { params }
  )
  if (!response.data.data) throw new Error("Payment trend not available.")
  return response.data.data
}

export function usePaymentTrend(params: AnalyticsParams & { interval: TimeseriesInterval }) {
  return useQuery({
    queryKey: ["payments", "analytics-trend", params],
    queryFn: () => fetchPaymentTrend(params),
    placeholderData: (previous) => previous,
  })
}

async function fetchPaymentMethodBreakdown(
  params: AnalyticsParams
): Promise<CashfreePaymentMethodBreakdown> {
  const response = await apiClient.get<ApiResponse<CashfreePaymentMethodBreakdown>>(
    "/payments/analytics/method-breakdown",
    { params }
  )
  if (!response.data.data) throw new Error("Payment method breakdown not available.")
  return response.data.data
}

export function usePaymentMethodBreakdown(params: AnalyticsParams) {
  return useQuery({
    queryKey: ["payments", "analytics-method-breakdown", params],
    queryFn: () => fetchPaymentMethodBreakdown(params),
    placeholderData: (previous) => previous,
  })
}

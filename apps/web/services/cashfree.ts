import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import axios from "axios"

import { apiClient } from "@/lib/api-client"
import type { ApiResponse } from "@/types/api"
import type { AnalyticsDateRangeParams, TimeseriesInterval } from "@/types/analytics"
import type {
  CashfreeCheckout,
  CashfreeConnectionTest,
  CashfreePaymentMethodBreakdown,
  CashfreePaymentOverview,
  CashfreePaymentStatus,
  CashfreePaymentTrend,
  CashfreeSettlementSummary,
  CashfreeStatus,
  CashfreeSyncRequest,
  CashfreeSyncResult,
} from "@/types/cashfree"

async function fetchCashfreePayment(orderId: string): Promise<CashfreePaymentStatus | null> {
  try {
    const response = await apiClient.get<ApiResponse<CashfreePaymentStatus>>(
      `/payments/cashfree/orders/${orderId}`
    )
    return response.data.data
  } catch (error) {
    // No Cashfree checkout has been started for this order yet -- an
    // ordinary, expected state, not an error to surface to the user.
    if (axios.isAxiosError(error) && error.response?.status === 404) {
      return null
    }
    throw error
  }
}

export function useCashfreePayment(orderId: string) {
  return useQuery({
    queryKey: ["cashfree-payment", "order", orderId],
    queryFn: () => fetchCashfreePayment(orderId),
    enabled: Boolean(orderId),
  })
}

export function useCreateCashfreeCheckout(orderId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async () => {
      // Deliberately no request body -- the amount is always computed
      // server-side from the OMS order; the frontend never supplies or
      // overrides it.
      const response = await apiClient.post<ApiResponse<CashfreeCheckout>>(
        `/payments/cashfree/orders/${orderId}/create`,
        {}
      )
      return response.data.data
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["cashfree-payment", "order", orderId] })
    },
  })
}

export function useReconcileCashfreePayment(orderId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async () => {
      const response = await apiClient.post<ApiResponse<CashfreePaymentStatus>>(
        `/payments/cashfree/orders/${orderId}/reconcile`,
        {}
      )
      return response.data.data
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["cashfree-payment", "order", orderId] })
      void queryClient.invalidateQueries({ queryKey: ["orders", orderId] })
    },
  })
}

// --- Connection status (payments dashboard) ---------------------------

async function fetchCashfreeStatus(): Promise<CashfreeStatus> {
  const response = await apiClient.get<ApiResponse<CashfreeStatus>>("/payments/cashfree/status")
  if (!response.data.data) throw new Error("Cashfree status not available.")
  return response.data.data
}

/** Pure config snapshot -- no live Cashfree call, safe to load on every
 * dashboard visit.
 */
export function useCashfreeStatus() {
  return useQuery({
    queryKey: ["cashfree-status"],
    queryFn: fetchCashfreeStatus,
  })
}

/** On-demand only ("Test Connection" button) -- makes one real,
 * read-only Cashfree API call server-side. Never auto-run/polled.
 */
export function useTestCashfreeConnection() {
  return useMutation({
    mutationFn: async () => {
      const response = await apiClient.post<ApiResponse<CashfreeConnectionTest>>(
        "/payments/cashfree/status/test-connection",
        {}
      )
      return response.data.data
    },
  })
}

// --- Payment analytics (payments dashboard) ----------------------------

async function fetchCashfreePaymentOverview(
  params: AnalyticsDateRangeParams
): Promise<CashfreePaymentOverview> {
  const response = await apiClient.get<ApiResponse<CashfreePaymentOverview>>(
    "/payments/cashfree/analytics/overview",
    { params }
  )
  if (!response.data.data) throw new Error("Cashfree payment overview not available.")
  return response.data.data
}

export function useCashfreePaymentOverview(params: AnalyticsDateRangeParams) {
  return useQuery({
    queryKey: ["cashfree-payment-overview", params],
    queryFn: () => fetchCashfreePaymentOverview(params),
    placeholderData: (previous) => previous,
  })
}

async function fetchCashfreePaymentTrend(
  params: AnalyticsDateRangeParams & { interval: TimeseriesInterval }
): Promise<CashfreePaymentTrend> {
  const response = await apiClient.get<ApiResponse<CashfreePaymentTrend>>(
    "/payments/cashfree/analytics/trend",
    { params }
  )
  if (!response.data.data) throw new Error("Cashfree payment trend not available.")
  return response.data.data
}

export function useCashfreePaymentTrend(
  params: AnalyticsDateRangeParams & { interval: TimeseriesInterval }
) {
  return useQuery({
    queryKey: ["cashfree-payment-trend", params],
    queryFn: () => fetchCashfreePaymentTrend(params),
    placeholderData: (previous) => previous,
  })
}

async function fetchCashfreePaymentMethodBreakdown(
  params: AnalyticsDateRangeParams
): Promise<CashfreePaymentMethodBreakdown> {
  const response = await apiClient.get<ApiResponse<CashfreePaymentMethodBreakdown>>(
    "/payments/cashfree/analytics/method-breakdown",
    { params }
  )
  if (!response.data.data) throw new Error("Cashfree payment method breakdown not available.")
  return response.data.data
}

export function useCashfreePaymentMethodBreakdown(params: AnalyticsDateRangeParams) {
  return useQuery({
    queryKey: ["cashfree-payment-method-breakdown", params],
    queryFn: () => fetchCashfreePaymentMethodBreakdown(params),
    placeholderData: (previous) => previous,
  })
}

// --- Bulk transaction/settlement sync (operator-triggered only) --------
// Neither of these is ever called automatically -- both back a single
// "Sync ..." button click (spec: no background auto-polling).

function invalidateCashfreeAnalytics(queryClient: ReturnType<typeof useQueryClient>) {
  void queryClient.invalidateQueries({ queryKey: ["cashfree-payment-overview"] })
  void queryClient.invalidateQueries({ queryKey: ["cashfree-payment-trend"] })
  void queryClient.invalidateQueries({ queryKey: ["cashfree-payment-method-breakdown"] })
  void queryClient.invalidateQueries({ queryKey: ["payments", "list"] })
}

export function useSyncCashfreeTransactions() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (payload: CashfreeSyncRequest) => {
      const response = await apiClient.post<ApiResponse<CashfreeSyncResult>>(
        "/payments/cashfree/sync",
        payload
      )
      if (!response.data.data) throw new Error("Cashfree sync did not return a result.")
      return response.data.data
    },
    onSuccess: () => invalidateCashfreeAnalytics(queryClient),
  })
}

export function useSyncCashfreeSettlements() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (payload: CashfreeSyncRequest) => {
      const response = await apiClient.post<ApiResponse<CashfreeSyncResult>>(
        "/payments/cashfree/settlements/sync",
        payload
      )
      if (!response.data.data) throw new Error("Cashfree settlement sync did not return a result.")
      return response.data.data
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["cashfree-settlement-summary"] })
    },
  })
}

async function fetchCashfreeSettlementSummary(): Promise<CashfreeSettlementSummary> {
  const response = await apiClient.get<ApiResponse<CashfreeSettlementSummary>>(
    "/payments/cashfree/analytics/settlements"
  )
  if (!response.data.data) throw new Error("Cashfree settlement summary not available.")
  return response.data.data
}

/** Reads only the locally-synced settlement table -- run
 * `useSyncCashfreeSettlements` first to populate/refresh it.
 */
export function useCashfreeSettlementSummary() {
  return useQuery({
    queryKey: ["cashfree-settlement-summary"],
    queryFn: fetchCashfreeSettlementSummary,
  })
}

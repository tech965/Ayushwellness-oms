import { useQuery } from "@tanstack/react-query"

import { apiClient } from "@/lib/api-client"
import type { ApiResponse } from "@/types/api"
import type {
  AnalyticsDateRangeParams,
  AnalyticsSummary,
  Breakdowns,
  CourierPerformance,
  OrdersTimeseries,
  PaymentStatusBreakdown,
  PaymentStatusTimeseries,
  RecentActivity,
  ReturnsRefundsSummary,
  RevenueTimeseries,
  TimeseriesInterval,
  TopProduct,
} from "@/types/analytics"

/** Shared by every dashboard analytics hook below: `refetchInterval`
 * drives the dashboard's "Refresh interval" setting (Administration ->
 * Settings -> Dashboard) -- `false`/`undefined` means no polling (the
 * default, matching every non-dashboard consumer of these hooks, e.g.
 * the standalone Analytics page).
 */
interface QueryLifecycleOptions {
  enabled?: boolean
  refetchInterval?: number | false
}

async function fetchAnalyticsSummary(
  params: AnalyticsDateRangeParams
): Promise<AnalyticsSummary> {
  const response = await apiClient.get<ApiResponse<AnalyticsSummary>>(
    "/analytics/summary",
    {
      params,
    }
  )
  if (!response.data.data) throw new Error("Analytics summary not available.")
  return response.data.data
}

export function useAnalyticsSummary(
  params: AnalyticsDateRangeParams,
  options?: QueryLifecycleOptions
) {
  return useQuery({
    queryKey: ["analytics", "summary", params],
    queryFn: () => fetchAnalyticsSummary(params),
    placeholderData: (previous) => previous,
    enabled: options?.enabled ?? true,
    refetchInterval: options?.refetchInterval ?? false,
  })
}

async function fetchOrdersTimeseries(
  params: AnalyticsDateRangeParams & { interval: TimeseriesInterval }
): Promise<OrdersTimeseries> {
  const response = await apiClient.get<ApiResponse<OrdersTimeseries>>(
    "/analytics/orders-timeseries",
    { params }
  )
  if (!response.data.data) throw new Error("Timeseries not available.")
  return response.data.data
}

export function useOrdersTimeseries(
  params: AnalyticsDateRangeParams & { interval: TimeseriesInterval },
  options?: QueryLifecycleOptions
) {
  return useQuery({
    queryKey: ["analytics", "orders-timeseries", params],
    queryFn: () => fetchOrdersTimeseries(params),
    placeholderData: (previous) => previous,
    enabled: options?.enabled ?? true,
    refetchInterval: options?.refetchInterval ?? false,
  })
}

async function fetchBreakdowns(params: AnalyticsDateRangeParams): Promise<Breakdowns> {
  const response = await apiClient.get<ApiResponse<Breakdowns>>("/analytics/breakdowns", {
    params,
  })
  if (!response.data.data) throw new Error("Breakdowns not available.")
  return response.data.data
}

export function useBreakdowns(
  params: AnalyticsDateRangeParams,
  options?: QueryLifecycleOptions
) {
  return useQuery({
    queryKey: ["analytics", "breakdowns", params],
    queryFn: () => fetchBreakdowns(params),
    placeholderData: (previous) => previous,
    refetchInterval: options?.refetchInterval ?? false,
  })
}

async function fetchTopProducts(
  params: AnalyticsDateRangeParams & { limit?: number }
): Promise<TopProduct[]> {
  const response = await apiClient.get<ApiResponse<TopProduct[]>>(
    "/analytics/top-products",
    {
      params,
    }
  )
  return response.data.data ?? []
}

export function useTopProducts(
  params: AnalyticsDateRangeParams & { limit?: number },
  options?: QueryLifecycleOptions
) {
  return useQuery({
    queryKey: ["analytics", "top-products", params],
    queryFn: () => fetchTopProducts(params),
    placeholderData: (previous) => previous,
    refetchInterval: options?.refetchInterval ?? false,
  })
}

async function fetchCourierPerformance(
  params: AnalyticsDateRangeParams
): Promise<CourierPerformance[]> {
  const response = await apiClient.get<ApiResponse<CourierPerformance[]>>(
    "/analytics/couriers",
    { params }
  )
  return response.data.data ?? []
}

export function useCourierPerformance(
  params: AnalyticsDateRangeParams,
  options?: QueryLifecycleOptions
) {
  return useQuery({
    queryKey: ["analytics", "couriers", params],
    queryFn: () => fetchCourierPerformance(params),
    placeholderData: (previous) => previous,
    refetchInterval: options?.refetchInterval ?? false,
  })
}

async function fetchPaymentStatusBreakdown(
  params: AnalyticsDateRangeParams & { payment_type?: "cod" | "prepaid" }
): Promise<PaymentStatusBreakdown> {
  const response = await apiClient.get<ApiResponse<PaymentStatusBreakdown>>(
    "/analytics/payment-status-breakdown",
    { params }
  )
  if (!response.data.data) throw new Error("Payment status breakdown not available.")
  return response.data.data
}

export function usePaymentStatusBreakdown(
  params: AnalyticsDateRangeParams & { payment_type?: "cod" | "prepaid" }
) {
  return useQuery({
    queryKey: ["analytics", "payment-status-breakdown", params],
    queryFn: () => fetchPaymentStatusBreakdown(params),
    placeholderData: (previous) => previous,
  })
}

async function fetchRevenueTimeseries(
  params: AnalyticsDateRangeParams & { interval: TimeseriesInterval }
): Promise<RevenueTimeseries> {
  const response = await apiClient.get<ApiResponse<RevenueTimeseries>>(
    "/analytics/revenue-timeseries",
    { params }
  )
  if (!response.data.data) throw new Error("Revenue timeseries not available.")
  return response.data.data
}

export function useRevenueTimeseries(
  params: AnalyticsDateRangeParams & { interval: TimeseriesInterval }
) {
  return useQuery({
    queryKey: ["analytics", "revenue-timeseries", params],
    queryFn: () => fetchRevenueTimeseries(params),
    placeholderData: (previous) => previous,
  })
}

async function fetchPaymentStatusTimeseries(
  params: AnalyticsDateRangeParams & {
    interval: TimeseriesInterval
    payment_type: "cod" | "prepaid"
  }
): Promise<PaymentStatusTimeseries> {
  const response = await apiClient.get<ApiResponse<PaymentStatusTimeseries>>(
    "/analytics/payment-status-timeseries",
    { params }
  )
  if (!response.data.data) throw new Error("Payment status timeseries not available.")
  return response.data.data
}

export function usePaymentStatusTimeseries(
  params: AnalyticsDateRangeParams & {
    interval: TimeseriesInterval
    payment_type: "cod" | "prepaid"
  }
) {
  return useQuery({
    queryKey: ["analytics", "payment-status-timeseries", params],
    queryFn: () => fetchPaymentStatusTimeseries(params),
    placeholderData: (previous) => previous,
  })
}

async function fetchReturnsRefundsSummary(
  params: AnalyticsDateRangeParams
): Promise<ReturnsRefundsSummary> {
  const response = await apiClient.get<ApiResponse<ReturnsRefundsSummary>>(
    "/analytics/returns-refunds",
    { params }
  )
  if (!response.data.data) throw new Error("Returns/refunds summary not available.")
  return response.data.data
}

export function useReturnsRefundsSummary(
  params: AnalyticsDateRangeParams,
  options?: QueryLifecycleOptions
) {
  return useQuery({
    queryKey: ["analytics", "returns-refunds", params],
    queryFn: () => fetchReturnsRefundsSummary(params),
    placeholderData: (previous) => previous,
    refetchInterval: options?.refetchInterval ?? false,
  })
}

async function fetchRecentActivity(): Promise<RecentActivity> {
  const response = await apiClient.get<ApiResponse<RecentActivity>>(
    "/analytics/recent-activity"
  )
  if (!response.data.data) throw new Error("Recent activity not available.")
  return response.data.data
}

export function useRecentActivity(options?: QueryLifecycleOptions) {
  return useQuery({
    queryKey: ["analytics", "recent-activity"],
    queryFn: fetchRecentActivity,
    refetchInterval: options?.refetchInterval ?? false,
  })
}

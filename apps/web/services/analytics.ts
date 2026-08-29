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
  RevenueTimeseries,
  TimeseriesInterval,
  TopProduct,
} from "@/types/analytics"

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

export function useAnalyticsSummary(params: AnalyticsDateRangeParams) {
  return useQuery({
    queryKey: ["analytics", "summary", params],
    queryFn: () => fetchAnalyticsSummary(params),
    placeholderData: (previous) => previous,
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
  params: AnalyticsDateRangeParams & { interval: TimeseriesInterval }
) {
  return useQuery({
    queryKey: ["analytics", "orders-timeseries", params],
    queryFn: () => fetchOrdersTimeseries(params),
    placeholderData: (previous) => previous,
  })
}

async function fetchBreakdowns(params: AnalyticsDateRangeParams): Promise<Breakdowns> {
  const response = await apiClient.get<ApiResponse<Breakdowns>>("/analytics/breakdowns", {
    params,
  })
  if (!response.data.data) throw new Error("Breakdowns not available.")
  return response.data.data
}

export function useBreakdowns(params: AnalyticsDateRangeParams) {
  return useQuery({
    queryKey: ["analytics", "breakdowns", params],
    queryFn: () => fetchBreakdowns(params),
    placeholderData: (previous) => previous,
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

export function useTopProducts(params: AnalyticsDateRangeParams & { limit?: number }) {
  return useQuery({
    queryKey: ["analytics", "top-products", params],
    queryFn: () => fetchTopProducts(params),
    placeholderData: (previous) => previous,
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

export function useCourierPerformance(params: AnalyticsDateRangeParams) {
  return useQuery({
    queryKey: ["analytics", "couriers", params],
    queryFn: () => fetchCourierPerformance(params),
    placeholderData: (previous) => previous,
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

async function fetchRecentActivity(): Promise<RecentActivity> {
  const response = await apiClient.get<ApiResponse<RecentActivity>>(
    "/analytics/recent-activity"
  )
  if (!response.data.data) throw new Error("Recent activity not available.")
  return response.data.data
}

export function useRecentActivity() {
  return useQuery({
    queryKey: ["analytics", "recent-activity"],
    queryFn: fetchRecentActivity,
  })
}

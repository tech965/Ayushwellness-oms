import { useQuery } from "@tanstack/react-query"

import { apiClient } from "@/lib/api-client"
import type { ApiResponse } from "@/types/api"
import type {
  AnalyticsDateRangeParams,
  AnalyticsSummary,
  Breakdowns,
  CourierPerformance,
  OrdersTimeseries,
  RecentActivity,
  TimeseriesInterval,
  TopProduct,
} from "@/types/analytics"

async function fetchAnalyticsSummary(
  params: AnalyticsDateRangeParams
): Promise<AnalyticsSummary> {
  const response = await apiClient.get<ApiResponse<AnalyticsSummary>>("/analytics/summary", {
    params,
  })
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
  const response = await apiClient.get<ApiResponse<TopProduct[]>>("/analytics/top-products", {
    params,
  })
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

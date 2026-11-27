import { useQuery } from "@tanstack/react-query"

import { apiClient } from "@/lib/api-client"
import type { ApiResponse, PaginatedResponse } from "@/types/api"
import type {
  AnalyticsDateRangeParams,
  AnalyticsSummary,
  Breakdowns,
  CodDashboard,
  CourierPerformance,
  FulfillmentPaymentBreakdown,
  FulfillmentPaymentClassification,
  FulfillmentPaymentOrderRow,
  NdrReasonBreakdown,
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
    enabled: options?.enabled ?? true,
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
    enabled: options?.enabled ?? true,
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
    enabled: options?.enabled ?? true,
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
    enabled: options?.enabled ?? true,
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
    enabled: options?.enabled ?? true,
    refetchInterval: options?.refetchInterval ?? false,
  })
}

async function fetchFulfillmentPaymentBreakdown(
  params: AnalyticsDateRangeParams
): Promise<FulfillmentPaymentBreakdown> {
  const response = await apiClient.get<ApiResponse<FulfillmentPaymentBreakdown>>(
    "/analytics/fulfillment-payment-breakdown",
    { params }
  )
  if (!response.data.data) throw new Error("Fulfillment/payment breakdown not available.")
  return response.data.data
}

export function useFulfillmentPaymentBreakdown(
  params: AnalyticsDateRangeParams,
  options?: QueryLifecycleOptions
) {
  return useQuery({
    queryKey: ["analytics", "fulfillment-payment-breakdown", params],
    queryFn: () => fetchFulfillmentPaymentBreakdown(params),
    placeholderData: (previous) => previous,
    enabled: options?.enabled ?? true,
    refetchInterval: options?.refetchInterval ?? false,
  })
}

interface FulfillmentPaymentClassificationParams extends AnalyticsDateRangeParams {
  fulfillmentStatus: "fulfilled" | "unfulfilled"
  paymentType: "cod" | "prepaid"
  /** Only fetch once the user has actually expanded a COD/Prepaid KPI. */
  enabled?: boolean
}

export function useFulfillmentPaymentClassification(
  params: FulfillmentPaymentClassificationParams
) {
  const { fulfillmentStatus, paymentType, enabled = true, ...dateRange } = params
  return useQuery({
    queryKey: [
      "analytics",
      "fulfillment-payment-classification",
      { fulfillmentStatus, paymentType, ...dateRange },
    ],
    queryFn: async (): Promise<FulfillmentPaymentClassification> => {
      const response = await apiClient.get<ApiResponse<FulfillmentPaymentClassification>>(
        "/analytics/fulfillment-payment-classification",
        { params: { ...dateRange, fulfillment_status: fulfillmentStatus, payment_type: paymentType } }
      )
      if (!response.data.data) throw new Error("Classification not available.")
      return response.data.data
    },
    enabled,
  })
}

interface NdrReasonBreakdownParams extends AnalyticsDateRangeParams {
  fulfillmentStatus?: "fulfilled" | "unfulfilled"
  paymentType?: "cod" | "prepaid"
  enabled?: boolean
}

export function useNdrReasonBreakdown(params: NdrReasonBreakdownParams) {
  const { fulfillmentStatus, paymentType, enabled = true, ...dateRange } = params
  return useQuery({
    queryKey: [
      "analytics",
      "fulfillment-payment-ndr-reasons",
      { fulfillmentStatus, paymentType, ...dateRange },
    ],
    queryFn: async (): Promise<NdrReasonBreakdown> => {
      const response = await apiClient.get<ApiResponse<NdrReasonBreakdown>>(
        "/analytics/fulfillment-payment-ndr-reasons",
        {
          params: {
            ...dateRange,
            fulfillment_status: fulfillmentStatus,
            payment_type: paymentType,
          },
        }
      )
      if (!response.data.data) throw new Error("NDR reason breakdown not available.")
      return response.data.data
    },
    enabled,
  })
}

async function fetchCodDashboard(params: AnalyticsDateRangeParams): Promise<CodDashboard> {
  const response = await apiClient.get<ApiResponse<CodDashboard>>("/analytics/cod-dashboard", {
    params,
  })
  if (!response.data.data) throw new Error("COD dashboard not available.")
  return response.data.data
}

export function useCodDashboard(
  params: AnalyticsDateRangeParams,
  options?: QueryLifecycleOptions
) {
  return useQuery({
    queryKey: ["analytics", "cod-dashboard", params],
    queryFn: () => fetchCodDashboard(params),
    placeholderData: (previous) => previous,
    enabled: options?.enabled ?? true,
    refetchInterval: options?.refetchInterval ?? false,
  })
}

/** Keeps the previous page on screen only while paging/searching inside the
 * SAME selection+filters -- same rule as `useShipmentStatusDrilldown`'s
 * `keepPreviousForSameScope`, duplicated locally since that one lives in
 * `services/shipments.ts` and isn't exported.
 */
function keepPreviousForSameFulfillmentPaymentScope<T>(scope: unknown) {
  return (previous: T | undefined, previousQuery?: { queryKey: readonly unknown[] }) =>
    previousQuery && JSON.stringify(previousQuery.queryKey.at(-2)) === JSON.stringify(scope)
      ? previous
      : undefined
}

interface FulfillmentPaymentOrdersParams extends AnalyticsDateRangeParams {
  unifiedStatus?: string
  fulfillmentStatus?: "fulfilled" | "unfulfilled"
  paymentType?: "cod" | "prepaid"
  q?: string
  page: number
  pageSize: number
  /** Only fetch once a KPI/classification row has actually been selected. */
  enabled?: boolean
}

/** Order-level drill-down shared by the Fulfilled/Unfulfilled
 * classification rows AND every COD Orders dashboard KPI -- same
 * endpoint, same guarantee that a drill-down always matches the count on
 * the row/card that opened it (see `GET /analytics/fulfillment-payment-
 * orders`'s docstring).
 */
export function useFulfillmentPaymentOrders(params: FulfillmentPaymentOrdersParams) {
  const {
    unifiedStatus,
    fulfillmentStatus,
    paymentType,
    q,
    page,
    pageSize,
    enabled = true,
    ...dateRange
  } = params
  const selector = { unifiedStatus, fulfillmentStatus, paymentType, ...dateRange }
  return useQuery({
    queryKey: ["analytics", "fulfillment-payment-orders", selector, { q, page, pageSize }],
    queryFn: async (): Promise<PaginatedResponse<FulfillmentPaymentOrderRow>> => {
      const response = await apiClient.get<PaginatedResponse<FulfillmentPaymentOrderRow>>(
        "/analytics/fulfillment-payment-orders",
        {
          params: {
            ...dateRange,
            unified_status: unifiedStatus,
            fulfillment_status: fulfillmentStatus,
            payment_type: paymentType,
            q: q || undefined,
            page,
            page_size: pageSize,
          },
        }
      )
      return response.data
    },
    enabled,
    placeholderData: keepPreviousForSameFulfillmentPaymentScope<
      PaginatedResponse<FulfillmentPaymentOrderRow>
    >(selector),
  })
}

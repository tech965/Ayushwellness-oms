import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import { apiClient } from "@/lib/api-client"
import type { ApiResponse, PaginatedResponse } from "@/types/api"
import type {
  DeliveryPerformance,
  DeliveryPerformanceFilters,
  DeliveryTrend,
  FulfilmentPerformanceFilters,
  FulfilmentSummary,
  FulfilmentTrend,
  FulfilmentScope,
  PartnerAllocation,
  PartnerFilters,
  PartnerMetric,
  PartnerPerformance,
  PerformanceOrderRow,
  StatusGroupKey,
  StatusKpiKey,
  Shipment,
  ShipmentEvent,
  ShipmentListFilters,
  ShipmentStatusAnalytics,
  ShipmentStatusAnalyticsFilters,
  ShipmentStatusDrilldownRow,
  ShipmentStatusHistory,
  UnifiedShipmentStatus,
} from "@/types/shipment"

interface ListParams extends ShipmentListFilters {
  page: number
  pageSize: number
}

async function fetchShipments(params: ListParams): Promise<PaginatedResponse<Shipment>> {
  const response = await apiClient.get<PaginatedResponse<Shipment>>("/shipments", {
    params: {
      page: params.page,
      page_size: params.pageSize,
      q: params.q || undefined,
      status: params.status,
      courier_id: params.courier_id,
      date_from: params.date_from,
      date_to: params.date_to,
    },
  })
  return response.data
}

export function useShipments(params: ListParams) {
  return useQuery({
    queryKey: ["shipments", params],
    queryFn: () => fetchShipments(params),
    placeholderData: (previous) => previous,
  })
}

async function fetchShipment(id: string): Promise<Shipment> {
  const response = await apiClient.get<ApiResponse<Shipment>>(`/shipments/${id}`)
  if (!response.data.data) throw new Error("Shipment not found.")
  return response.data.data
}

export function useShipment(id: string) {
  return useQuery({
    queryKey: ["shipments", id],
    queryFn: () => fetchShipment(id),
    enabled: Boolean(id),
  })
}

async function fetchShipmentTimeline(id: string): Promise<ShipmentEvent[]> {
  const response = await apiClient.get<ApiResponse<ShipmentEvent[]>>(
    `/shipments/${id}/timeline`
  )
  return response.data.data ?? []
}

export function useShipmentTimeline(id: string) {
  return useQuery({
    queryKey: ["shipments", id, "timeline"],
    queryFn: () => fetchShipmentTimeline(id),
    enabled: Boolean(id),
  })
}

async function fetchShipmentsForOrder(orderId: string): Promise<Shipment[]> {
  const response = await apiClient.get<PaginatedResponse<Shipment>>("/shipments", {
    params: { order_id: orderId, page_size: 50 },
  })
  return response.data.data
}

export function useShipmentsForOrder(orderId: string) {
  return useQuery({
    queryKey: ["shipments", "order", orderId],
    queryFn: () => fetchShipmentsForOrder(orderId),
    enabled: Boolean(orderId),
  })
}

/** A shipment action (AWB assignment, cancel, pickup, tracking refresh)
 * can change what "Orders Need Shipment"/"Confirmed by Telecaller" show
 * for this order too (e.g. cancelling reopens it for re-shipping) --
 * invalidating `["orders"]` alongside `["shipments"]` keeps those pages
 * correct without a manual reload. Matches `services/shipment-queue.ts`'s
 * `invalidateAfterShipmentChange`.
 */
function useShiprocketAction(id: string, path: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (courierId?: string) => {
      const response = await apiClient.post<ApiResponse<Shipment>>(
        `/shipments/${id}/shiprocket/${path}`,
        path === "assign-awb" ? { courier_id: courierId ?? null } : undefined
      )
      return response.data.data
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["shipments"] })
      void queryClient.invalidateQueries({ queryKey: ["shipment-queue"] })
      void queryClient.invalidateQueries({ queryKey: ["orders"] })
    },
  })
}

export function useAssignAwb(id: string) {
  return useShiprocketAction(id, "assign-awb")
}

export function useCancelShipment(id: string) {
  return useShiprocketAction(id, "cancel")
}

export function useRequestPickup(id: string) {
  return useShiprocketAction(id, "request-pickup")
}

export function useRefreshTracking(id: string) {
  return useShiprocketAction(id, "refresh-tracking")
}

/** Manually retries the outbound Shopify fulfillment push (Part 13's
 * "Retry Shopify Sync" action). Always resolves 200 -- a still-failed
 * retry comes back as a normal success response whose
 * `shopify_sync_status` is still `"failed"`, never a rejected promise.
 */
export function useRetryShopifySync(id: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async () => {
      const response = await apiClient.post<ApiResponse<Shipment>>(
        `/shipments/${id}/shopify/retry-sync`
      )
      return response.data.data
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["shipments"] })
      void queryClient.invalidateQueries({ queryKey: ["shipment-queue"] })
      void queryClient.invalidateQueries({ queryKey: ["orders"] })
    },
  })
}

// --- OMS SOW Module 2: Shipment Tracking & Status Analytics ---------------

export function useShipmentStatusAnalytics(filters: ShipmentStatusAnalyticsFilters) {
  return useQuery({
    queryKey: ["shipments", "status-analytics", filters],
    queryFn: async (): Promise<ShipmentStatusAnalytics> => {
      const response = await apiClient.get<ApiResponse<ShipmentStatusAnalytics>>(
        "/shipments/status-analytics",
        { params: filters }
      )
      if (!response.data.data) throw new Error("Shipment status analytics not found.")
      return response.data.data
    },
  })
}

export function useShipmentStatusHistory(filters: ShipmentStatusAnalyticsFilters) {
  return useQuery({
    queryKey: ["shipments", "status-analytics", "history", filters],
    queryFn: async (): Promise<ShipmentStatusHistory> => {
      const response = await apiClient.get<ApiResponse<ShipmentStatusHistory>>(
        "/shipments/status-analytics/history",
        { params: filters }
      )
      if (!response.data.data) throw new Error("Shipment status history not found.")
      return response.data.data
    },
  })
}

interface DrilldownParams extends ShipmentStatusAnalyticsFilters {
  /** Exactly one of these selects what to list. */
  unifiedStatus?: UnifiedShipmentStatus
  group?: StatusGroupKey
  kpi?: StatusKpiKey
  q?: string
  page: number
  pageSize: number
  /** Only fetch once a KPI / category has actually been selected. */
  enabled?: boolean
}

export function useShipmentStatusDrilldown(params: DrilldownParams) {
  const {
    unifiedStatus,
    group,
    kpi,
    q,
    page,
    pageSize,
    enabled = true,
    ...filters
  } = params
  const selector = { unified_status: unifiedStatus, group, kpi }
  return useQuery({
    queryKey: [
      "shipments",
      "status-analytics",
      "orders",
      selector,
      q,
      page,
      pageSize,
      filters,
    ],
    queryFn: async (): Promise<PaginatedResponse<ShipmentStatusDrilldownRow>> => {
      const response = await apiClient.get<PaginatedResponse<ShipmentStatusDrilldownRow>>(
        "/shipments/status-analytics/orders",
        {
          params: {
            ...filters,
            ...selector,
            q: q || undefined,
            page,
            page_size: pageSize,
          },
        }
      )
      return response.data
    },
    enabled: enabled && Boolean(unifiedStatus || group || kpi),
    placeholderData: (previous) => previous,
  })
}

/** Triggers the browser's native download for the Excel export --
 * `apiClient` isn't used here since this needs the raw `blob` response
 * type and a manually constructed anchor click, not a parsed JSON body.
 */
export async function downloadShipmentStatusHistory(
  filters: ShipmentStatusAnalyticsFilters
): Promise<void> {
  const response = await apiClient.get("/shipments/status-analytics/history/export", {
    params: filters,
    responseType: "blob",
  })
  const url = window.URL.createObjectURL(new Blob([response.data]))
  const link = document.createElement("a")
  link.href = url
  link.download = "shipment-status-history.xlsx"
  document.body.appendChild(link)
  link.click()
  link.remove()
  window.URL.revokeObjectURL(url)
}

// --- OMS SOW Module 3: Fulfilment & Delivery Performance Dashboard --------

export function useFulfilmentPerformance(
  filters: FulfilmentPerformanceFilters,
  enabled = true
) {
  return useQuery({
    queryKey: ["shipments", "fulfilment-performance", filters],
    queryFn: async (): Promise<FulfilmentSummary> => {
      const response = await apiClient.get<ApiResponse<FulfilmentSummary>>(
        "/shipments/fulfilment-performance",
        { params: filters }
      )
      if (!response.data.data) throw new Error("Fulfilment performance not found.")
      return response.data.data
    },
    enabled,
  })
}

export function useFulfilmentPerformanceHistory(filters: FulfilmentPerformanceFilters) {
  return useQuery({
    queryKey: ["shipments", "fulfilment-performance", "history", filters],
    queryFn: async (): Promise<FulfilmentTrend> => {
      const response = await apiClient.get<ApiResponse<FulfilmentTrend>>(
        "/shipments/fulfilment-performance/history",
        { params: filters }
      )
      if (!response.data.data)
        throw new Error("Fulfilment performance history not found.")
      return response.data.data
    },
  })
}

export function useDeliveryPerformance(
  filters: DeliveryPerformanceFilters,
  enabled = true
) {
  return useQuery({
    queryKey: ["shipments", "delivery-performance", filters],
    queryFn: async (): Promise<DeliveryPerformance> => {
      const response = await apiClient.get<ApiResponse<DeliveryPerformance>>(
        "/shipments/delivery-performance",
        { params: filters }
      )
      if (!response.data.data) throw new Error("Delivery performance not found.")
      return response.data.data
    },
    enabled,
  })
}

export function useDeliveryPerformanceHistory(filters: DeliveryPerformanceFilters) {
  return useQuery({
    queryKey: ["shipments", "delivery-performance", "history", filters],
    queryFn: async (): Promise<DeliveryTrend> => {
      const response = await apiClient.get<ApiResponse<DeliveryTrend>>(
        "/shipments/delivery-performance/history",
        { params: filters }
      )
      if (!response.data.data) throw new Error("Delivery performance history not found.")
      return response.data.data
    },
  })
}

// --- OMS SOW Module 4: Shipping Partner Allocation & Performance Analysis -

export function usePartnerAllocation(filters: PartnerFilters) {
  return useQuery({
    queryKey: ["shipments", "partner-allocation", filters],
    queryFn: async (): Promise<PartnerAllocation> => {
      const response = await apiClient.get<ApiResponse<PartnerAllocation>>(
        "/shipments/partner-allocation",
        { params: filters }
      )
      if (!response.data.data) throw new Error("Partner allocation not found.")
      return response.data.data
    },
  })
}

export function usePartnerPerformance(filters: PartnerFilters) {
  return useQuery({
    queryKey: ["shipments", "partner-performance", filters],
    queryFn: async (): Promise<PartnerPerformance> => {
      const response = await apiClient.get<ApiResponse<PartnerPerformance>>(
        "/shipments/partner-performance",
        { params: filters }
      )
      if (!response.data.data) throw new Error("Partner performance not found.")
      return response.data.data
    },
  })
}

// --- Order lists behind Module 3 / Module 4 cards, bars and rows ----------

interface PerformanceOrdersParams {
  page: number
  pageSize: number
  q?: string
  /** Only fetch once something has actually been selected. */
  enabled?: boolean
}

export function useFulfilmentOrders(
  params: PerformanceOrdersParams &
    FulfilmentPerformanceFilters & { scope: FulfilmentScope | null; day?: string }
) {
  const { scope, day, q, page, pageSize, enabled = true, ...filters } = params
  return useQuery({
    queryKey: [
      "shipments",
      "fulfilment-performance",
      "orders",
      scope,
      day,
      q,
      page,
      pageSize,
      filters,
    ],
    queryFn: async (): Promise<PaginatedResponse<PerformanceOrderRow>> => {
      const response = await apiClient.get<PaginatedResponse<PerformanceOrderRow>>(
        "/shipments/fulfilment-performance/orders",
        {
          params: {
            ...filters,
            scope,
            day,
            q: q || undefined,
            page,
            page_size: pageSize,
          },
        }
      )
      return response.data
    },
    enabled: enabled && scope !== null,
    placeholderData: (previous) => previous,
  })
}

export function usePartnerOrders(
  params: PerformanceOrdersParams &
    PartnerFilters & { metric: PartnerMetric | null; partner?: string }
) {
  const { metric, partner, q, page, pageSize, enabled = true, ...filters } = params
  return useQuery({
    queryKey: [
      "shipments",
      "partner-orders",
      metric,
      partner,
      q,
      page,
      pageSize,
      filters,
    ],
    queryFn: async (): Promise<PaginatedResponse<PerformanceOrderRow>> => {
      const response = await apiClient.get<PaginatedResponse<PerformanceOrderRow>>(
        "/shipments/partner-orders",
        {
          params: {
            ...filters,
            metric,
            partner,
            q: q || undefined,
            page,
            page_size: pageSize,
          },
        }
      )
      return response.data
    },
    enabled: enabled && metric !== null,
    placeholderData: (previous) => previous,
  })
}

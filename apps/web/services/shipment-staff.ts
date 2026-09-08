import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import { apiClient } from "@/lib/api-client"
import type { ApiResponse, PaginatedResponse } from "@/types/api"
import type { NDR, NDRListFilters } from "@/types/ndr"
import type { OrderDetail } from "@/types/order"
import type { RTO, RTOListFilters } from "@/types/rto"
import type {
  Shipment,
  ShipmentAnalytics,
  ShipmentEvent,
  ShipmentQueueFilters,
  ShipmentQueueRow,
  ShipmentSummary,
} from "@/types/shipment"

/** Every hook here calls the SCOPED `/shipment-staff/*` endpoints —
 * mirrors `services/shipment-queue.ts`/`services/shipments.ts` exactly
 * (same response shapes, reused unchanged), the only difference is the
 * backend restricts every result to Telecallers assigned to the caller
 * (see `ShipmentStaffService.resolve_scope`). No scope parameter exists
 * on any of these calls — there is nothing for a client to widen.
 */

interface ShipmentQueueParams extends ShipmentQueueFilters {
  page: number
  pageSize: number
}

async function fetchMyConfirmedOrders(
  params: ShipmentQueueParams
): Promise<PaginatedResponse<ShipmentQueueRow>> {
  const response = await apiClient.get<PaginatedResponse<ShipmentQueueRow>>(
    "/shipment-staff/orders",
    {
      params: {
        page: params.page,
        page_size: params.pageSize,
        q: params.q || undefined,
        payment_type: params.payment_type || undefined,
        courier_id: params.courier_id || undefined,
        sku: params.sku || undefined,
        shipment_status: params.shipment_status || undefined,
        date_from: params.date_from || undefined,
        date_to: params.date_to || undefined,
      },
    }
  )
  return response.data
}

export function useMyConfirmedOrders(params: ShipmentQueueParams) {
  return useQuery({
    queryKey: ["shipment-staff", "orders", params],
    queryFn: () => fetchMyConfirmedOrders(params),
    placeholderData: (previous) => previous,
    refetchOnWindowFocus: true,
  })
}

async function fetchMyConfirmedOrder(orderId: string): Promise<OrderDetail> {
  const response = await apiClient.get<ApiResponse<OrderDetail>>(
    `/shipment-staff/orders/${orderId}`
  )
  if (!response.data.data) throw new Error("Order not found.")
  return response.data.data
}

export function useMyConfirmedOrder(orderId: string) {
  return useQuery({
    queryKey: ["shipment-staff", "orders", orderId],
    queryFn: () => fetchMyConfirmedOrder(orderId),
    enabled: Boolean(orderId),
  })
}

/** Takes the order id at call time (`mutate(orderId)`), not at hook
 * instantiation -- this is used from a table with many rows, and a hook
 * can't be created inside a per-row cell callback (see
 * `useShipOrderFromQueue` in services/shipment-queue.ts for the
 * identical pattern/reasoning on the Fulfillment side).
 */
export function useShipMyConfirmedOrder() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (orderId: string) => {
      const response = await apiClient.post<ApiResponse<Shipment>>(
        `/shipment-staff/orders/${orderId}/ship`,
        {}
      )
      return response.data.data
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["shipment-staff"] })
    },
  })
}

interface MyShipmentsParams {
  page: number
  pageSize: number
  q?: string
  status?: string
  date_from?: string
  date_to?: string
}

async function fetchMyShipments(
  params: MyShipmentsParams
): Promise<PaginatedResponse<Shipment>> {
  const response = await apiClient.get<PaginatedResponse<Shipment>>("/shipment-staff/shipments", {
    params: {
      page: params.page,
      page_size: params.pageSize,
      q: params.q || undefined,
      status: params.status || undefined,
      date_from: params.date_from || undefined,
      date_to: params.date_to || undefined,
    },
  })
  return response.data
}

export function useMyShipments(params: MyShipmentsParams) {
  return useQuery({
    queryKey: ["shipment-staff", "shipments", params],
    queryFn: () => fetchMyShipments(params),
    placeholderData: (previous) => previous,
  })
}

async function fetchMyShipment(id: string): Promise<Shipment> {
  const response = await apiClient.get<ApiResponse<Shipment>>(`/shipment-staff/shipments/${id}`)
  if (!response.data.data) throw new Error("Shipment not found.")
  return response.data.data
}

export function useMyShipment(id: string) {
  return useQuery({
    queryKey: ["shipment-staff", "shipments", id],
    queryFn: () => fetchMyShipment(id),
    enabled: Boolean(id),
  })
}

async function fetchMyShipmentTimeline(id: string): Promise<ShipmentEvent[]> {
  const response = await apiClient.get<ApiResponse<ShipmentEvent[]>>(
    `/shipment-staff/shipments/${id}/timeline`
  )
  return response.data.data ?? []
}

export function useMyShipmentTimeline(id: string) {
  return useQuery({
    queryKey: ["shipment-staff", "shipments", id, "timeline"],
    queryFn: () => fetchMyShipmentTimeline(id),
    enabled: Boolean(id),
  })
}

function useMyShipmentAction(id: string, path: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (courierId?: string) => {
      const response = await apiClient.post<ApiResponse<Shipment>>(
        `/shipment-staff/shipments/${id}/${path}`,
        path === "assign-awb" ? { courier_id: courierId ?? null } : undefined
      )
      return response.data.data
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["shipment-staff"] })
    },
  })
}

export function useMyAssignAwb(id: string) {
  return useMyShipmentAction(id, "assign-awb")
}

export function useMyRequestPickup(id: string) {
  return useMyShipmentAction(id, "request-pickup")
}

export function useMyRefreshTracking(id: string) {
  return useMyShipmentAction(id, "refresh-tracking")
}

export function useMyRetryShopifySync(id: string) {
  return useMyShipmentAction(id, "shopify/retry-sync")
}

async function fetchMySummary(): Promise<ShipmentSummary> {
  const response = await apiClient.get<ApiResponse<ShipmentSummary>>("/shipment-staff/summary")
  if (!response.data.data) throw new Error("Shipment summary not available.")
  return response.data.data
}

export function useMyShipmentSummary() {
  return useQuery({
    queryKey: ["shipment-staff", "summary"],
    queryFn: fetchMySummary,
    refetchOnWindowFocus: true,
  })
}

interface ShipmentAnalyticsParams {
  date_from?: string
  date_to?: string
}

async function fetchMyAnalytics(params: ShipmentAnalyticsParams): Promise<ShipmentAnalytics> {
  const response = await apiClient.get<ApiResponse<ShipmentAnalytics>>(
    "/shipment-staff/analytics",
    { params: { date_from: params.date_from || undefined, date_to: params.date_to || undefined } }
  )
  if (!response.data.data) throw new Error("Shipment analytics not available.")
  return response.data.data
}

export function useMyShipmentAnalytics(params: ShipmentAnalyticsParams = {}) {
  return useQuery({
    queryKey: ["shipment-staff", "analytics", params],
    queryFn: () => fetchMyAnalytics(params),
    refetchOnWindowFocus: true,
  })
}

interface MyListParams {
  page: number
  pageSize: number
}

async function fetchMyNdr(
  params: MyListParams & NDRListFilters
): Promise<PaginatedResponse<NDR>> {
  const response = await apiClient.get<PaginatedResponse<NDR>>("/shipment-staff/ndr", {
    params: {
      page: params.page,
      page_size: params.pageSize,
      q: params.q || undefined,
      status: params.status || undefined,
      date_from: params.date_from || undefined,
      date_to: params.date_to || undefined,
    },
  })
  return response.data
}

export function useMyNdr(params: MyListParams & NDRListFilters) {
  return useQuery({
    queryKey: ["shipment-staff", "ndr", params],
    queryFn: () => fetchMyNdr(params),
    placeholderData: (previous) => previous,
  })
}

async function fetchMyRto(
  params: MyListParams & RTOListFilters
): Promise<PaginatedResponse<RTO>> {
  const response = await apiClient.get<PaginatedResponse<RTO>>("/shipment-staff/rto", {
    params: {
      page: params.page,
      page_size: params.pageSize,
      q: params.q || undefined,
      status: params.status || undefined,
      date_from: params.date_from || undefined,
      date_to: params.date_to || undefined,
    },
  })
  return response.data
}

export function useMyRto(params: MyListParams & RTOListFilters) {
  return useQuery({
    queryKey: ["shipment-staff", "rto", params],
    queryFn: () => fetchMyRto(params),
    placeholderData: (previous) => previous,
  })
}

export interface ShipmentStaffPerformance {
  shipment_staff_id: string
  shipment_staff_name: string
  telecaller_count: number
  confirmed: number
  shipped: number
  delivered: number
  ndr: number
  rto: number
}

async function fetchShipmentStaffPerformance(): Promise<ShipmentStaffPerformance[]> {
  const response = await apiClient.get<ApiResponse<ShipmentStaffPerformance[]>>(
    "/shipment-staff/admin/performance"
  )
  return response.data.data ?? []
}

/** ADMIN/OPERATIONS/MANAGEMENT oversight (Part 7) -- deliberately NOT
 * scoped, gated on `shipments.read` server-side (see
 * `get_shipment_staff_performance`), never reachable from within a
 * Shipment Staff user's own scope.
 */
export function useShipmentStaffPerformance(enabled = true) {
  return useQuery({
    queryKey: ["shipment-staff", "admin", "performance"],
    queryFn: fetchShipmentStaffPerformance,
    enabled,
  })
}

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import { apiClient } from "@/lib/api-client"
import type { ApiResponse, PaginatedResponse } from "@/types/api"
import type { Shipment, ShipmentEvent, ShipmentListFilters } from "@/types/shipment"

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

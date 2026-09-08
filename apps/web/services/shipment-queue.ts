import { useQuery } from "@tanstack/react-query"

import { apiClient } from "@/lib/api-client"
import type { ApiResponse, PaginatedResponse } from "@/types/api"
import type {
  ShipmentAnalytics,
  ShipmentQueueFilters,
  ShipmentQueueRow,
  ShipmentSummary,
} from "@/types/shipment"

interface ShipmentQueueParams extends ShipmentQueueFilters {
  page: number
  pageSize: number
}

async function fetchShipmentQueue(
  params: ShipmentQueueParams
): Promise<PaginatedResponse<ShipmentQueueRow>> {
  const response = await apiClient.get<PaginatedResponse<ShipmentQueueRow>>("/shipments/queue", {
    params: {
      page: params.page,
      page_size: params.pageSize,
      q: params.q || undefined,
      payment_type: params.payment_type || undefined,
      telecaller_id: params.telecaller_id || undefined,
      courier_id: params.courier_id || undefined,
      sku: params.sku || undefined,
      shipment_status: params.shipment_status || undefined,
      date_from: params.date_from || undefined,
      date_to: params.date_to || undefined,
    },
  })
  return response.data
}

/** Confirmed orders awaiting shipment processing -- a distinct, narrower
 * view from `useShipments` (every already-processed shipment).
 */
export function useShipmentQueue(params: ShipmentQueueParams) {
  return useQuery({
    queryKey: ["shipment-queue", params],
    queryFn: () => fetchShipmentQueue(params),
    placeholderData: (previous) => previous,
  })
}

async function fetchShipmentSummary(): Promise<ShipmentSummary> {
  const response = await apiClient.get<ApiResponse<ShipmentSummary>>("/shipments/summary")
  if (!response.data.data) throw new Error("Shipment summary not available.")
  return response.data.data
}

export function useShipmentSummary() {
  return useQuery({
    queryKey: ["shipments", "summary"],
    queryFn: fetchShipmentSummary,
  })
}

interface ShipmentAnalyticsParams {
  date_from?: string
  date_to?: string
}

async function fetchShipmentAnalytics(
  params: ShipmentAnalyticsParams
): Promise<ShipmentAnalytics> {
  const response = await apiClient.get<ApiResponse<ShipmentAnalytics>>("/shipments/analytics", {
    params: { date_from: params.date_from || undefined, date_to: params.date_to || undefined },
  })
  if (!response.data.data) throw new Error("Shipment analytics not available.")
  return response.data.data
}

export function useShipmentAnalytics(params: ShipmentAnalyticsParams = {}) {
  return useQuery({
    queryKey: ["shipments", "analytics", params],
    queryFn: () => fetchShipmentAnalytics(params),
  })
}

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import { apiClient } from "@/lib/api-client"
import type { ApiResponse, PaginatedResponse } from "@/types/api"
import type {
  BulkShipOrdersResponse,
  Shipment,
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
 *
 * `refetchOnWindowFocus: true` overrides the app-wide default (`false`,
 * see `lib/query-client.ts`) specifically here: this data is routinely
 * changed by a *different person* (a Telecaller confirming an order) in a
 * different login/browser tab entirely, which no query-key invalidation
 * from this tab's own mutations can ever reach. Tabbing back to a
 * Fulfillment Queue page left open across a shift must show newly
 * confirmed orders without a manual hard refresh.
 */
export function useShipmentQueue(params: ShipmentQueueParams) {
  return useQuery({
    queryKey: ["shipment-queue", params],
    queryFn: () => fetchShipmentQueue(params),
    placeholderData: (previous) => previous,
    refetchOnWindowFocus: true,
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
    refetchOnWindowFocus: true,
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
    refetchOnWindowFocus: true,
  })
}

/** Row-level "Ship via Shiprocket" from the Fulfillment Queue -- the order
 * id is passed at call time (`mutate(orderId)`) rather than baked into the
 * hook, since one table renders many rows and a hook can't be created
 * inside a per-row cell callback. Distinct from `useShipOrderViaShiprocket`
 * in `services/orders.ts` (the order-detail page's single bound-id hook) --
 * same underlying endpoint, different call shape for a different caller.
 */
/** A brand-new shipment moves an order between all three fulfillment
 * views at once: it leaves "Orders Need Shipment" (`shipment-queue`),
 * appears/updates in "Shipments" (`shipments`), and its status changes
 * in "Confirmed by Telecaller" (`orders`, incl. the `confirmed-by-
 * telecaller` query -- query-key prefix matching invalidates that too).
 * Refreshing all three here (rather than each page invalidating only
 * its own key) is what makes "ship from the Confirmed-by-Telecaller
 * table" and "ship from the queue" both keep every other open view
 * correct without a manual page reload.
 */
function invalidateAfterShipmentChange(queryClient: ReturnType<typeof useQueryClient>) {
  void queryClient.invalidateQueries({ queryKey: ["shipment-queue"] })
  void queryClient.invalidateQueries({ queryKey: ["shipments"] })
  void queryClient.invalidateQueries({ queryKey: ["orders"] })
}

export function useShipOrderFromQueue() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (orderId: string) => {
      const response = await apiClient.post<ApiResponse<Shipment>>(`/orders/${orderId}/ship`, {})
      return response.data.data
    },
    onSuccess: () => invalidateAfterShipmentChange(queryClient),
  })
}

/** Read-only dry run for the bulk-ship confirmation screen -- classifies
 * every selected order as ready/not-ready with a reason, WITHOUT
 * creating anything (`POST /orders/bulk-ship/validate`). Not a mutation
 * despite the POST verb (no server-side write happens); modeled as one
 * anyway so the confirmation dialog gets `isPending` for free, same as
 * every other async action in this codebase.
 */
export function useValidateBulkShip() {
  return useMutation({
    mutationFn: async (orderIds: string[]) => {
      const response = await apiClient.post<
        ApiResponse<{ order_id: string; ready: boolean; reason: string | null }[]>
      >("/orders/bulk-ship/validate", { order_ids: orderIds })
      return response.data.data ?? []
    },
  })
}

/** Ships every selected confirmed order independently -- the response
 * always reports a per-order result (insufficient stock, unknown order,
 * etc. never blocks the rest), same convention as `useBulkConfirmOrders`.
 */
export function useBulkShipOrders() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (orderIds: string[]) => {
      const response = await apiClient.post<ApiResponse<BulkShipOrdersResponse>>(
        "/orders/bulk-ship",
        { order_ids: orderIds }
      )
      if (!response.data.data) throw new Error("Bulk ship did not return a result.")
      return response.data.data
    },
    onSuccess: () => invalidateAfterShipmentChange(queryClient),
  })
}

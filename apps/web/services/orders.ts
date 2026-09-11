import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import { apiClient } from "@/lib/api-client"
import type { ApiResponse, PaginatedResponse } from "@/types/api"
import type {
  Order,
  OrderDetail,
  OrderEvent,
  OrderListFilters,
  OrderStatus,
} from "@/types/order"
import type { ProcessExistingShipmentsResponse } from "@/types/shipment"

interface ListParams extends OrderListFilters {
  page: number
  pageSize: number
  sortBy?: string
  sortOrder?: "asc" | "desc"
}

function toOrderQueryParams(params: OrderListFilters) {
  return {
    q: params.q || undefined,
    status: params.status,
    payment_status: params.payment_status,
    payment_type: params.payment_type,
    fulfillment_status: params.fulfillment_status,
    shipment_status: params.shipment_status,
    courier_id: params.courier_id,
    sku: params.sku,
    tag: params.tag,
    amount_min: params.amount_min || undefined,
    amount_max: params.amount_max || undefined,
    date_from: params.date_from,
    date_to: params.date_to,
    confirmed_only: params.confirmed_only || undefined,
    telecaller_id: params.telecaller_id || undefined,
    confirmed_date_from: params.confirmed_date_from || undefined,
    confirmed_date_to: params.confirmed_date_to || undefined,
  }
}

async function fetchOrders(params: ListParams): Promise<PaginatedResponse<Order>> {
  const response = await apiClient.get<PaginatedResponse<Order>>("/orders", {
    params: {
      page: params.page,
      page_size: params.pageSize,
      sort_by: params.sortBy,
      sort_order: params.sortOrder,
      ...toOrderQueryParams(params),
    },
  })
  return response.data
}

async function downloadOrdersExport(filters: OrderListFilters): Promise<void> {
  const response = await apiClient.get("/orders/export", {
    params: toOrderQueryParams(filters),
    responseType: "blob",
  })
  const url = window.URL.createObjectURL(new Blob([response.data]))
  const link = document.createElement("a")
  link.href = url
  link.download = "orders-export.xlsx"
  document.body.appendChild(link)
  link.click()
  link.remove()
  window.URL.revokeObjectURL(url)
}

/** Downloads the same filtered set as `useOrders`, unpaginated, as a real
 * `.xlsx` workbook (`GET /orders/export`) — no pagination params, capped
 * server-side at `ExportService.MAX_ROWS`. Wrapped as a mutation (rather
 * than a plain async call) purely so the Export button gets `isPending`
 * for free, matching every other async action in this codebase.
 */
export function useExportOrders() {
  return useMutation({
    mutationFn: downloadOrdersExport,
  })
}

export function useOrders(params: ListParams) {
  return useQuery({
    queryKey: ["orders", params],
    queryFn: () => fetchOrders(params),
    placeholderData: (previous) => previous,
  })
}

/** "Confirmed by Telecaller" -- a history/operational view of every order
 * `confirmed_by_telecaller_id IS NOT NULL` has ever been set on, with NO
 * restriction on current order/fulfillment/shipment status (unlike
 * `useShipmentQueue`'s "needs shipment now" business rule -- a shipped or
 * delivered order stays visible here). Thin wrapper over the same
 * `fetchOrders`/`GET /orders` `useOrders` already uses -- `confirmed_only`
 * is just pinned `true`, nothing duplicated. A distinct query key keeps
 * this cached separately from the general Orders list.
 */
export function useTelecallerConfirmedOrders(params: Omit<ListParams, "confirmed_only">) {
  return useQuery({
    queryKey: ["orders", "confirmed-by-telecaller", params],
    queryFn: () => fetchOrders({ ...params, confirmed_only: true }),
    placeholderData: (previous) => previous,
    refetchOnWindowFocus: true,
  })
}

async function fetchOrder(id: string): Promise<OrderDetail> {
  const response = await apiClient.get<ApiResponse<OrderDetail>>(`/orders/${id}`)
  if (!response.data.data) throw new Error("Order not found.")
  return response.data.data
}

export function useOrder(id: string) {
  return useQuery({
    queryKey: ["orders", id],
    queryFn: () => fetchOrder(id),
    enabled: Boolean(id),
  })
}

async function fetchOrderTimeline(id: string): Promise<OrderEvent[]> {
  const response = await apiClient.get<ApiResponse<OrderEvent[]>>(
    `/orders/${id}/timeline`
  )
  return response.data.data ?? []
}

export function useOrderTimeline(id: string) {
  return useQuery({
    queryKey: ["orders", id, "timeline"],
    queryFn: () => fetchOrderTimeline(id),
    enabled: Boolean(id),
  })
}

/** Explicitly (re)validates this order's shipping address right now --
 * the Order Details page's "Validate Address" action, and the one way
 * an order that predates this feature (`shipping_address_validation_
 * status` still `null`, shown as "Validation pending") gets a real
 * result without waiting for its address to next change. Deliberately
 * NOT called automatically by any list/queue page -- see `POST
 * /orders/{id}/validate-address`'s backend docstring for why validation
 * must never happen as a side effect of a page load.
 */
export function useValidateOrderAddress(id: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async () => {
      const response = await apiClient.post<ApiResponse<OrderDetail>>(
        `/orders/${id}/validate-address`
      )
      return response.data.data
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["orders", id] })
      void queryClient.invalidateQueries({ queryKey: ["orders"] })
      void queryClient.invalidateQueries({ queryKey: ["shipment-queue"] })
      void queryClient.invalidateQueries({ queryKey: ["shipment-staff"] })
    },
  })
}

export function useTransitionOrderStatus(id: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (input: { status: OrderStatus; description?: string }) => {
      const response = await apiClient.patch<ApiResponse<OrderDetail>>(
        `/orders/${id}`,
        input
      )
      return response.data.data
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["orders", id] })
    },
  })
}

/** Process Shipment / Ship Order -- the API equivalent of Shiprocket's own
 * dashboard "Bulk Ship Orders" action, for orders that already have an
 * EXISTING Shiprocket order/shipment (`POST /orders/bulk-process-shipments`).
 * NEVER creates a Shiprocket order -- only resolves each order's existing
 * shipment and assigns an AWB to it, skipping any that already have one.
 * Used for both the single-order "Process Shipment"/"Ship Order" button (a
 * one-item `orderIds`) and the bulk Fulfillment Queue action -- every order
 * gets its own `status`/`reason` in the response, never all-or-nothing.
 */
export function useProcessExistingShipments() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (orderIds: string[]) => {
      const response = await apiClient.post<ApiResponse<ProcessExistingShipmentsResponse>>(
        "/orders/bulk-process-shipments",
        { order_ids: orderIds }
      )
      if (!response.data.data) throw new Error("Shipment processing response not available.")
      return response.data.data
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["shipment-queue"] })
      void queryClient.invalidateQueries({ queryKey: ["shipments"] })
      void queryClient.invalidateQueries({ queryKey: ["orders"] })
    },
  })
}

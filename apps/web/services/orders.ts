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
import type { Shipment } from "@/types/shipment"

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
    shipment_status: params.shipment_status,
    courier_id: params.courier_id,
    sku: params.sku,
    amount_min: params.amount_min || undefined,
    amount_max: params.amount_max || undefined,
    date_from: params.date_from,
    date_to: params.date_to,
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

export function useShipOrderViaShiprocket(id: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async () => {
      const response = await apiClient.post<ApiResponse<Shipment>>(
        `/orders/${id}/ship`,
        {}
      )
      return response.data.data
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["shipments", "order", id] })
    },
  })
}

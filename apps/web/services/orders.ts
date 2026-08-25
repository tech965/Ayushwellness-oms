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
}

async function fetchOrders(params: ListParams): Promise<PaginatedResponse<Order>> {
  const response = await apiClient.get<PaginatedResponse<Order>>("/orders", {
    params: {
      page: params.page,
      page_size: params.pageSize,
      q: params.q || undefined,
      status: params.status,
      payment_status: params.payment_status,
      date_from: params.date_from,
      date_to: params.date_to,
    },
  })
  return response.data
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

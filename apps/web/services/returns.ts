import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import { apiClient } from "@/lib/api-client"
import type { ApiResponse, PaginatedResponse } from "@/types/api"
import type { Return, ReturnListFilters, ReturnStatus } from "@/types/return"

async function fetchReturnsForOrder(orderId: string): Promise<Return[]> {
  const response = await apiClient.get<PaginatedResponse<Return>>("/returns", {
    params: { order_id: orderId, page_size: 50 },
  })
  return response.data.data
}

export function useReturnsForOrder(orderId: string) {
  return useQuery({
    queryKey: ["returns", "order", orderId],
    queryFn: () => fetchReturnsForOrder(orderId),
    enabled: Boolean(orderId),
  })
}

interface ListParams extends ReturnListFilters {
  page: number
  pageSize: number
}

async function fetchReturns(params: ListParams): Promise<PaginatedResponse<Return>> {
  const response = await apiClient.get<PaginatedResponse<Return>>("/returns", {
    params: {
      page: params.page,
      page_size: params.pageSize,
      q: params.q || undefined,
      status: params.status,
      payment_type: params.payment_type,
      customer_id: params.customer_id,
      order_id: params.order_id,
      date_from: params.date_from,
      date_to: params.date_to,
    },
  })
  return response.data
}

/** Operational Returns list (the `/returns` page) — separate from
 * `useReturnsForOrder` above, which stays as the order-detail page's own
 * narrow "returns for this one order" query and is untouched by this.
 */
export function useReturns(params: ListParams) {
  return useQuery({
    queryKey: ["returns", "list", params],
    queryFn: () => fetchReturns(params),
    placeholderData: (previous) => previous,
  })
}

export function useUpdateReturn(id: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (input: { status?: ReturnStatus; notes?: string }) => {
      const response = await apiClient.patch<ApiResponse<Return>>(`/returns/${id}`, input)
      return response.data.data
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["returns"] })
      void queryClient.invalidateQueries({ queryKey: ["refunds"] })
    },
  })
}

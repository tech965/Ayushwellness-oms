import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import { apiClient } from "@/lib/api-client"
import type { ApiResponse, PaginatedResponse } from "@/types/api"
import type { Return, ReturnStatus } from "@/types/return"

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

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import { apiClient } from "@/lib/api-client"
import type { ApiResponse, PaginatedResponse } from "@/types/api"
import type { RTO, RTOListFilters, RTOStatus } from "@/types/rto"

interface ListParams extends RTOListFilters {
  page: number
  pageSize: number
}

async function fetchRtos(params: ListParams): Promise<PaginatedResponse<RTO>> {
  const response = await apiClient.get<PaginatedResponse<RTO>>("/rto", {
    params: {
      page: params.page,
      page_size: params.pageSize,
      q: params.q || undefined,
      status: params.status,
      payment_type: params.payment_type,
      courier_id: params.courier_id,
      date_from: params.date_from,
      date_to: params.date_to,
    },
  })
  return response.data
}

export function useRtos(params: ListParams) {
  return useQuery({
    queryKey: ["rto", params],
    queryFn: () => fetchRtos(params),
    placeholderData: (previous) => previous,
  })
}

async function fetchRto(id: string): Promise<RTO> {
  const response = await apiClient.get<ApiResponse<RTO>>(`/rto/${id}`)
  if (!response.data.data) throw new Error("RTO not found.")
  return response.data.data
}

export function useRto(id: string) {
  return useQuery({
    queryKey: ["rto", id],
    queryFn: () => fetchRto(id),
    enabled: Boolean(id),
  })
}

export function useUpdateRto(id: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (input: { status?: RTOStatus; notes?: string }) => {
      const response = await apiClient.patch<ApiResponse<RTO>>(`/rto/${id}`, input)
      return response.data.data
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["rto"] })
    },
  })
}

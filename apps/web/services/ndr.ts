import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import { apiClient } from "@/lib/api-client"
import type { ApiResponse, PaginatedResponse } from "@/types/api"
import type { NDR, NDRListFilters, NDRStatus } from "@/types/ndr"

interface ListParams extends NDRListFilters {
  page: number
  pageSize: number
}

async function fetchNdrs(params: ListParams): Promise<PaginatedResponse<NDR>> {
  const response = await apiClient.get<PaginatedResponse<NDR>>("/ndr", {
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

export function useNdrs(params: ListParams) {
  return useQuery({
    queryKey: ["ndr", params],
    queryFn: () => fetchNdrs(params),
    placeholderData: (previous) => previous,
  })
}

async function fetchNdr(id: string): Promise<NDR> {
  const response = await apiClient.get<ApiResponse<NDR>>(`/ndr/${id}`)
  if (!response.data.data) throw new Error("NDR not found.")
  return response.data.data
}

export function useNdr(id: string) {
  return useQuery({
    queryKey: ["ndr", id],
    queryFn: () => fetchNdr(id),
    enabled: Boolean(id),
  })
}

export function useUpdateNdr(id: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (input: { status?: NDRStatus; notes?: string }) => {
      const response = await apiClient.patch<ApiResponse<NDR>>(`/ndr/${id}`, input)
      return response.data.data
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["ndr"] })
    },
  })
}

export function useNdrReattempt(id: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (input: {
      address_1: string
      address_2?: string
      phone: string
    }) => {
      const response = await apiClient.post<ApiResponse<NDR>>(
        `/ndr/${id}/reattempt`,
        input
      )
      return response.data.data
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["ndr"] })
    },
  })
}

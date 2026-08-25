import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import { apiClient } from "@/lib/api-client"
import type { ApiResponse, PaginatedResponse } from "@/types/api"
import type {
  ReconciliationResult,
  ReconciliationResultListFilters,
  ReconciliationRun,
} from "@/types/reconciliation"

interface ListParams {
  page: number
  pageSize: number
}

async function fetchReconciliationRuns(
  params: ListParams
): Promise<PaginatedResponse<ReconciliationRun>> {
  const response = await apiClient.get<PaginatedResponse<ReconciliationRun>>(
    "/reconciliation/runs",
    { params: { page: params.page, page_size: params.pageSize } }
  )
  return response.data
}

export function useReconciliationRuns(params: ListParams) {
  return useQuery({
    queryKey: ["reconciliation", "runs", params],
    queryFn: () => fetchReconciliationRuns(params),
    placeholderData: (previous) => previous,
  })
}

async function fetchReconciliationResults(
  params: ListParams & ReconciliationResultListFilters
): Promise<PaginatedResponse<ReconciliationResult>> {
  const response = await apiClient.get<PaginatedResponse<ReconciliationResult>>(
    "/reconciliation/results",
    {
      params: {
        page: params.page,
        page_size: params.pageSize,
        run_id: params.run_id,
        status: params.status,
        check_type: params.check_type,
        provider: params.provider,
        resolved: params.resolved,
      },
    }
  )
  return response.data
}

export function useReconciliationResults(
  params: ListParams & ReconciliationResultListFilters
) {
  return useQuery({
    queryKey: ["reconciliation", "results", params],
    queryFn: () => fetchReconciliationResults(params),
    placeholderData: (previous) => previous,
  })
}

export function useTriggerReconciliationRun() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async () => {
      const response =
        await apiClient.post<ApiResponse<ReconciliationRun>>("/reconciliation/runs")
      return response.data.data
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["reconciliation", "runs"] })
    },
  })
}

export function useResolveReconciliationResult() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (resultId: string) => {
      const response = await apiClient.post<ApiResponse<ReconciliationResult>>(
        `/reconciliation/results/${resultId}/resolve`
      )
      return response.data.data
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["reconciliation", "results"] })
    },
  })
}

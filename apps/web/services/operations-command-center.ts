import { useQuery } from "@tanstack/react-query"

import { apiClient } from "@/lib/api-client"
import type { ApiResponse } from "@/types/api"
import type {
  CommandCenterParams,
  OperationsCommandCenterResponse,
} from "@/types/operations-command-center"

async function fetchOperationsCommandCenter(
  params: CommandCenterParams
): Promise<OperationsCommandCenterResponse> {
  const response = await apiClient.get<ApiResponse<OperationsCommandCenterResponse>>(
    "/analytics/operations-command-center",
    { params }
  )
  if (!response.data.data) throw new Error("Operations Command Center data not available.")
  return response.data.data
}

/** Backs the whole Operations Command Center page with exactly one
 * request -- summary, attention items, operations health, business
 * opportunities, and insights all come back together.
 */
export function useOperationsCommandCenter(params: CommandCenterParams) {
  return useQuery({
    queryKey: ["analytics", "operations-command-center", params],
    queryFn: () => fetchOperationsCommandCenter(params),
    placeholderData: (previous) => previous,
  })
}

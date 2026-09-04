import { useQuery } from "@tanstack/react-query"

import { apiClient } from "@/lib/api-client"
import type { ApiResponse } from "@/types/api"
import type {
  SupplyIntelligenceParams,
  SupplyIntelligenceResponse,
} from "@/types/supply-intelligence"

async function fetchSupplyIntelligence(
  params: SupplyIntelligenceParams
): Promise<SupplyIntelligenceResponse> {
  const response = await apiClient.get<ApiResponse<SupplyIntelligenceResponse>>(
    "/analytics/supply-intelligence",
    { params }
  )
  if (!response.data.data) throw new Error("Supply intelligence data not available.")
  return response.data.data
}

/** Backs the whole India Supply Intelligence page with exactly one
 * request (summary + map/leaderboard data + insights all come back
 * together); re-fetches only when the date range or selected state
 * param changes -- selecting a state does not refetch the map/leaderboard,
 * only adds `selected_state` to the same response shape.
 */
export function useSupplyIntelligence(params: SupplyIntelligenceParams) {
  return useQuery({
    queryKey: ["analytics", "supply-intelligence", params],
    queryFn: () => fetchSupplyIntelligence(params),
    placeholderData: (previous) => previous,
  })
}

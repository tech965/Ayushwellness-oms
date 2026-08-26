import { useQuery } from "@tanstack/react-query"

import { apiClient } from "@/lib/api-client"
import type { PaginatedResponse } from "@/types/api"
import type { Courier } from "@/types/courier"

async function fetchCouriers(): Promise<Courier[]> {
  const response = await apiClient.get<PaginatedResponse<Courier>>("/couriers", {
    params: { page_size: 200 },
  })
  return response.data.data
}

/** Small, rarely-changing list — used to populate the Orders page's
 * courier filter dropdown. `page_size: 200` in one call rather than a
 * paginated picker, since courier counts are small by nature (a handful
 * of carrier accounts, not a growing business entity).
 */
export function useCouriers() {
  return useQuery({
    queryKey: ["couriers", "all"],
    queryFn: fetchCouriers,
    staleTime: 5 * 60 * 1000,
  })
}

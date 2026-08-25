import { useQuery } from "@tanstack/react-query"

import { apiClient } from "@/lib/api-client"
import type { PaginatedResponse } from "@/types/api"
import type { Refund } from "@/types/refund"

async function fetchRefundsForOrder(orderId: string): Promise<Refund[]> {
  const response = await apiClient.get<PaginatedResponse<Refund>>("/refunds", {
    params: { order_id: orderId, page_size: 50 },
  })
  return response.data.data
}

export function useRefundsForOrder(orderId: string) {
  return useQuery({
    queryKey: ["refunds", "order", orderId],
    queryFn: () => fetchRefundsForOrder(orderId),
    enabled: Boolean(orderId),
  })
}

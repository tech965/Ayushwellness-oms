import { useQuery } from "@tanstack/react-query"

import { apiClient } from "@/lib/api-client"
import type { PaginatedResponse } from "@/types/api"
import type { Payment } from "@/types/payment"

async function fetchPaymentsForOrder(orderId: string): Promise<Payment[]> {
  const response = await apiClient.get<PaginatedResponse<Payment>>("/payments", {
    params: { order_id: orderId, page_size: 50 },
  })
  return response.data.data
}

export function usePaymentsForOrder(orderId: string) {
  return useQuery({
    queryKey: ["payments", "order", orderId],
    queryFn: () => fetchPaymentsForOrder(orderId),
    enabled: Boolean(orderId),
  })
}

import { useQuery } from "@tanstack/react-query"

import { apiClient } from "@/lib/api-client"
import type { PaginatedResponse } from "@/types/api"
import type { Refund, RefundListFilters } from "@/types/refund"

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

interface ListParams extends RefundListFilters {
  page: number
  pageSize: number
}

async function fetchRefunds(params: ListParams): Promise<PaginatedResponse<Refund>> {
  const response = await apiClient.get<PaginatedResponse<Refund>>("/refunds", {
    params: {
      page: params.page,
      page_size: params.pageSize,
      q: params.q || undefined,
      status: params.status,
      payment_type: params.payment_type,
      order_id: params.order_id,
      date_from: params.date_from,
      date_to: params.date_to,
    },
  })
  return response.data
}

/** Operational Refunds list (the `/refunds` page) — separate from
 * `useRefundsForOrder` above, which stays as the order-detail page's own
 * narrow "refunds for this one order" query and is untouched by this.
 */
export function useRefunds(params: ListParams) {
  return useQuery({
    queryKey: ["refunds", "list", params],
    queryFn: () => fetchRefunds(params),
    placeholderData: (previous) => previous,
  })
}

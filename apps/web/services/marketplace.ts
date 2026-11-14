import { useQuery } from "@tanstack/react-query"

import { apiClient } from "@/lib/api-client"
import type { ApiResponse, PaginatedResponse } from "@/types/api"
import type { AnalyticsDateRangeParams } from "@/types/analytics"
import type {
  MarketplaceDashboardSummary,
  MarketplaceOrder,
  MarketplaceOrderFilters,
} from "@/types/marketplace"

interface ListParams extends MarketplaceOrderFilters {
  page: number
  pageSize: number
  sortBy?: string
  sortOrder?: "asc" | "desc"
}

function toMarketplaceOrderQueryParams(params: MarketplaceOrderFilters) {
  return {
    marketplace: params.marketplace,
    status: params.status,
    payment_status: params.payment_status,
    fulfillment_status: params.fulfillment_status,
    shipment_status: params.shipment_status,
    sku: params.sku,
    date_from: params.date_from,
    date_to: params.date_to,
    q: params.q || undefined,
  }
}

async function fetchMarketplaceOrders(
  params: ListParams
): Promise<PaginatedResponse<MarketplaceOrder>> {
  const response = await apiClient.get<PaginatedResponse<MarketplaceOrder>>(
    "/marketplace/orders",
    {
      params: {
        page: params.page,
        page_size: params.pageSize,
        sort_by: params.sortBy,
        sort_order: params.sortOrder,
        ...toMarketplaceOrderQueryParams(params),
      },
    }
  )
  return response.data
}

/** The "All Marketplace Orders"/"Amazon Orders"/"Flipkart Orders"/"Meesho
 * Orders" pages all use this same hook — the latter three just pass a
 * fixed `marketplace` filter (see `app/(dashboard)/marketplace/orders/
 * [marketplace]/page.tsx`), never a separate implementation.
 */
export function useMarketplaceOrders(params: ListParams) {
  return useQuery({
    queryKey: ["marketplace", "orders", params],
    queryFn: () => fetchMarketplaceOrders(params),
    placeholderData: (previous) => previous,
  })
}

async function fetchMarketplaceDashboardSummary(
  params: AnalyticsDateRangeParams
): Promise<MarketplaceDashboardSummary> {
  const response = await apiClient.get<ApiResponse<MarketplaceDashboardSummary>>(
    "/marketplace/dashboard/summary",
    { params }
  )
  if (!response.data.data) throw new Error("Marketplace dashboard summary not available.")
  return response.data.data
}

export function useMarketplaceDashboardSummary(params: AnalyticsDateRangeParams) {
  return useQuery({
    queryKey: ["marketplace", "dashboard", "summary", params],
    queryFn: () => fetchMarketplaceDashboardSummary(params),
    placeholderData: (previous) => previous,
  })
}

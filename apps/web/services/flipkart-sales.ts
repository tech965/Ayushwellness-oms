import { useQuery } from "@tanstack/react-query"

import { apiClient } from "@/lib/api-client"
import type { ApiResponse } from "@/types/api"
import type { FlipkartOrdersPage, FlipkartStatus } from "@/types/flipkart-sales"

const BASE = "/marketplaces/flipkart/sales"

export function useFlipkartStatus() {
  return useQuery({
    queryKey: ["flipkart-sales", "status"],
    queryFn: async () =>
      (await apiClient.get<ApiResponse<FlipkartStatus>>(`${BASE}/status`)).data.data!,
  })
}

export interface FlipkartOrdersParams {
  date_from?: string
  date_to?: string
  status?: string
  page_size?: number
  next_page_url?: string
}

export function useFlipkartOrders(params: FlipkartOrdersParams, enabled: boolean) {
  return useQuery({
    queryKey: ["flipkart-sales", "orders", params],
    enabled,
    queryFn: async () =>
      (
        await apiClient.get<ApiResponse<FlipkartOrdersPage>>(`${BASE}/orders`, { params })
      ).data.data!,
    retry: false,
  })
}

export async function downloadFlipkartOrdersExport(
  params: Omit<FlipkartOrdersParams, "page_size" | "next_page_url">
): Promise<void> {
  const response = await apiClient.get(`${BASE}/orders/export`, {
    params,
    responseType: "blob",
    timeout: 60_000,
  })
  const url = window.URL.createObjectURL(new Blob([response.data]))
  const link = document.createElement("a")
  link.href = url
  link.download = "flipkart-orders.xlsx"
  document.body.appendChild(link)
  link.click()
  link.remove()
  window.URL.revokeObjectURL(url)
}

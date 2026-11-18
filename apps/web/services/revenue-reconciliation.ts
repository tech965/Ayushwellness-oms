import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import { apiClient } from "@/lib/api-client"
import type { ApiResponse, PaginatedResponse } from "@/types/api"

// Mirrors app/services/cod_reconciliation_service.py. Decimals arrive as strings.

export interface Bucket {
  orders: number
  amount: string
}

export interface PaymentSplit {
  total: Bucket
  fulfilled: Bucket
  unfulfilled: Bucket
  partial: Bucket
}

export interface CodFinancials {
  expected_amount: string
  collected_amount: string
  remitted_amount: string
  pending_remittance: string
  bank_verified_amount: string
  shipped_orders: number
  orders_with_shiprocket_data: number
  orders_without_shiprocket_data: number
  remittance_last_synced_at: string | null
}

export interface RevenueSummary {
  data_source: "live" | "local"
  complete: boolean
  warnings: string[]
  total_revenue: string
  cod: PaymentSplit
  prepaid: PaymentSplit
  cod_financials: CodFinancials
}

export interface RevenueOrderRow {
  id: string
  order_number: string
  order_datetime: string
  customer_name: string | null
  customer_phone: string | null
  customer_email: string | null
  amount: string
  currency: string
  payment_type: string
  payment_status: string
  fulfillment_status: string
  shipment_status: string | null
  courier_name: string | null
  awb: string | null
  tracking_url: string | null
  expected_cod_amount: string | null
  collected_amount: string | null
  remitted_amount: string | null
  pending_remittance: string | null
  remittance_status: string | null
  settlement_date: string | null
  settlement_reference: string | null
  financial_data: "reported" | "no_data" | null
  bank_verified: boolean | null
  bank_credit_amount: string | null
  bank_credit_reference: string | null
  bank_credited_on: string | null
}

export interface RevenueOrderFilters {
  payment_type: "cod" | "prepaid"
  fulfillment?: "fulfilled" | "unfulfilled" | "partial"
  q?: string
  date_from?: string
  date_to?: string
}

const BASE = "/revenue-reconciliation"

export function useRevenueSummary(params: { date_from?: string; date_to?: string }) {
  return useQuery({
    queryKey: ["revenue-reconciliation", "summary", params],
    queryFn: async () => {
      const response = await apiClient.get<ApiResponse<RevenueSummary>>(`${BASE}/summary`, {
        params,
      })
      return response.data.data as RevenueSummary
    },
    placeholderData: (previous) => previous,
  })
}

export function useRevenueOrders(
  filters: RevenueOrderFilters & { page: number; pageSize: number },
  enabled = true
) {
  const { pageSize, ...rest } = filters
  return useQuery({
    queryKey: ["revenue-reconciliation", "orders", filters],
    enabled,
    queryFn: async () => {
      const response = await apiClient.get<PaginatedResponse<RevenueOrderRow>>(
        `${BASE}/orders`,
        { params: { ...rest, q: rest.q || undefined, page_size: pageSize } }
      )
      return response.data
    },
    placeholderData: (previous) => previous,
  })
}

async function downloadExport(filters: RevenueOrderFilters): Promise<void> {
  const response = await apiClient.get(`${BASE}/orders/export`, {
    params: { ...filters, q: filters.q || undefined },
    responseType: "blob",
  })
  const url = window.URL.createObjectURL(new Blob([response.data]))
  const link = document.createElement("a")
  link.href = url
  link.download = `${filters.payment_type}-revenue-reconciliation.xlsx`
  document.body.appendChild(link)
  link.click()
  link.remove()
  window.URL.revokeObjectURL(url)
}

export function useExportRevenueOrders() {
  return useMutation({ mutationFn: downloadExport })
}

export function useSyncCodRemittances() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async () => {
      const response = await apiClient.post<
        ApiResponse<{ created: number; updated: number; skipped: number }>
      >(`${BASE}/sync`)
      return response.data.data
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["revenue-reconciliation"] }),
  })
}

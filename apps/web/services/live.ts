import { useMutation, useQuery } from "@tanstack/react-query"

import { apiClient } from "@/lib/api-client"
import type { ApiResponse } from "@/types/api"
import type { LiveAnalyticsSummary } from "@/types/live"

/** Whether the deployment serves provider data live (no local copy).
 * Defaults to "not live" until the answer arrives or if it fails, so a
 * hiccup never swaps the stored-data screens for the live ones.
 */
export function useLiveMode() {
  return useQuery({
    queryKey: ["live-mode"],
    queryFn: async (): Promise<{ live_mode: boolean }> => {
      const response = await apiClient.get<ApiResponse<{ live_mode: boolean }>>(
        "/analytics/live/status"
      )
      return response.data.data ?? { live_mode: false }
    },
    staleTime: 5 * 60_000,
    retry: false,
  })
}

export interface LiveSummaryParams {
  date_from?: string
  date_to?: string
  max_pages?: number
}

export function useLiveAnalyticsSummary(params: LiveSummaryParams) {
  return useQuery({
    queryKey: ["analytics", "live-summary", params],
    queryFn: async (): Promise<LiveAnalyticsSummary> => {
      const response = await apiClient.get<ApiResponse<LiveAnalyticsSummary>>(
        "/analytics/live/summary",
        { params }
      )
      if (!response.data.data) throw new Error("Live summary not available.")
      return response.data.data
    },
    placeholderData: (previous) => previous,
  })
}

// --- Capabilities -----------------------------------------------------------

export interface LiveCapability {
  operation: string
  reason: string
}

/** Operations the live architecture intentionally does not support, with
 * the reason — shown instead of simulating success. */
export function useLiveCapabilities() {
  return useQuery({
    queryKey: ["live-capabilities"],
    queryFn: async (): Promise<LiveCapability[]> => {
      const response = await apiClient.get<ApiResponse<LiveCapability[]>>(
        "/analytics/live/capabilities"
      )
      return response.data.data ?? []
    },
    staleTime: 10 * 60_000,
    retry: false,
  })
}

// --- Live Cashfree ------------------------------------------------------------

export interface LiveBoundedResult {
  /** False when the bounded read stopped early: totals/rows are partial. */
  complete: boolean
  warnings: string[]
}

export interface LiveTransaction {
  event_type: string
  cashfree_order_id: string | null
  cf_payment_id: string | null
  status: string
  amount: string | null
  currency: string | null
  time: string | null
}

export interface LiveSettlement {
  cf_settlement_id: string | null
  status: string | null
  utr: string | null
  payment_amount: string | null
  amount_settled: string | null
  initiated_on: string | null
  processed_on: string | null
}

export interface LivePaymentSummary extends LiveBoundedResult {
  payments_received: string
  refunded: string
  by_status: { status: string; count: number; amount: string }[]
  daily_received: { date: string; amount: string }[]
}

export interface LiveDateRange {
  date_from?: string
  date_to?: string
}

function useLiveCashfreeQuery<T>(key: string, path: string, params: LiveDateRange) {
  return useQuery({
    queryKey: ["cashfree-live", key, params],
    queryFn: async (): Promise<T> => {
      const response = await apiClient.get<ApiResponse<T>>(`/payments/cashfree/live/${path}`, {
        params,
      })
      if (!response.data.data) throw new Error("No data returned.")
      return response.data.data
    },
    placeholderData: (previous) => previous,
    retry: false,
  })
}

export function useLivePaymentSummary(params: LiveDateRange) {
  return useLiveCashfreeQuery<LivePaymentSummary>("summary", "summary", params)
}

export function useLiveTransactions(params: LiveDateRange) {
  return useLiveCashfreeQuery<LiveBoundedResult & { items: LiveTransaction[] }>(
    "transactions",
    "transactions",
    params
  )
}

export function useLiveRefunds(params: LiveDateRange) {
  return useLiveCashfreeQuery<LiveBoundedResult & { items: LiveTransaction[] }>(
    "refunds",
    "refunds",
    params
  )
}

export function useLiveSettlements(params: LiveDateRange) {
  return useLiveCashfreeQuery<
    LiveBoundedResult & {
      items: LiveSettlement[]
      unsettled_amount: string
      unsettled_amount_is_derived: boolean
    }
  >("settlements", "settlements", params)
}

// --- Live Shiprocket NDR ------------------------------------------------------------

export interface LiveNdrItem {
  awb: string | null
  reason: string | null
  attempt_number: number
  courier_name: string | null
  shiprocket_order_id: string | null
  external_created_at: string | null
  /** Present only when this OMS knows the shipment. */
  shipment_id: string | null
  order_id: string | null
}

export function useLiveNdr(page: number) {
  return useQuery({
    queryKey: ["ndr-live", page],
    queryFn: async () => {
      const response = await apiClient.get<
        ApiResponse<{ items: LiveNdrItem[]; has_more: boolean; next_page: number | null }>
      >("/live/ndr", { params: { page } })
      if (!response.data.data) throw new Error("No data returned.")
      return response.data.data
    },
    placeholderData: (previous) => previous,
    retry: false,
  })
}

export function useLiveNdrReattempt() {
  return useMutation({
    mutationFn: async (input: {
      shipmentId: string
      address_1: string
      address_2?: string
      phone: string
    }) => {
      const { shipmentId, ...body } = input
      await apiClient.post(`/live/shipments/${shipmentId}/ndr-reattempt`, body)
    },
  })
}

// --- Checkout export -------------------------------------------------------------------

/** Downloads the abandoned-checkout pool as .xlsx. Resolves to whether the
 * workbook is INCOMPLETE (the server stopped reading Shopify early). */
export async function downloadCheckoutExport(params: {
  call_status?: string
  date_from?: string
  date_to?: string
}): Promise<{ incomplete: boolean }> {
  const response = await apiClient.get("/team/checkouts/export", {
    params,
    responseType: "blob",
  })
  const url = window.URL.createObjectURL(new Blob([response.data]))
  const link = document.createElement("a")
  link.href = url
  link.download = "abandoned-checkouts.xlsx"
  document.body.appendChild(link)
  link.click()
  link.remove()
  window.URL.revokeObjectURL(url)
  return { incomplete: response.headers["x-export-incomplete"] === "true" }
}

// --- Live catalogue + OMS inventory ----------------------------------------------

export interface OmsStock {
  variant_id: string
  oms_sku: string
  physical_quantity: number
  reserved_quantity: number
  available_to_promise: number
  pack_size: number
  packets_per_box: number
}

export interface LiveCatalogVariant {
  external_variant_id: string
  sku: string | null
  title: string | null
  price: string | null
  compare_at_price: string | null
  barcode: string | null
  image_url: string | null
  mapped: boolean
  /** Shopify's own count: informational only, never used as OMS stock. */
  shopify_inventory_quantity: number | null
  oms: OmsStock | null
}

export interface LiveCatalogProduct {
  external_product_id: string
  title: string
  description: string | null
  vendor: string | null
  product_type: string | null
  status: string | null
  image_url: string | null
  variants: LiveCatalogVariant[]
}

export interface LiveProductFilters {
  q?: string
  sku?: string
  status?: string
  mapped?: boolean
  cursor?: string
}

export function useLiveProducts(filters: LiveProductFilters) {
  return useQuery({
    queryKey: ["catalog-live", "products", filters],
    queryFn: async () => {
      const response = await apiClient.get<
        ApiResponse<{
          items: LiveCatalogProduct[]
          next_cursor: string | null
          has_more: boolean
          page_filtered: boolean
        }>
      >("/live/catalog/products", {
        params: {
          q: filters.q || undefined,
          sku: filters.sku || undefined,
          status: filters.status || undefined,
          mapped: filters.mapped,
          cursor: filters.cursor || undefined,
        },
      })
      if (!response.data.data) throw new Error("No data returned.")
      return response.data.data
    },
    placeholderData: (previous) => previous,
    retry: false,
  })
}

export function useAutoMapBySku() {
  return useMutation({
    mutationFn: async () => {
      const response = await apiClient.post<
        ApiResponse<{ mapped: string[]; unmatched: string[]; complete: boolean }>
      >("/live/catalog/mappings/auto-map-by-sku")
      return response.data.data
    },
  })
}

export interface InventoryOverviewRow extends OmsStock {
  oms_display_name: string | null
  mapped: boolean
  external_variant_id: string | null
  shopify_title: string | null
}

export function useInventoryOverview(page: number, q: string) {
  return useQuery({
    queryKey: ["catalog-live", "inventory-overview", page, q],
    queryFn: async () => {
      const response = await apiClient.get<
        ApiResponse<{
          items: InventoryOverviewRow[]
          total_items: number
          page: number
          page_size: number
          shopify_names_available: boolean
          warnings: string[]
        }>
      >("/live/catalog/inventory/overview", { params: { page, q: q || undefined } })
      if (!response.data.data) throw new Error("No data returned.")
      return response.data.data
    },
    placeholderData: (previous) => previous,
    retry: false,
  })
}

// --- Live order payment (Cashfree) -------------------------------------------------

export interface LiveOrderPayment {
  found: boolean
  paid: boolean
  outcome: "match" | "mismatch" | "not_found_in_cashfree"
  shopify_payment_status: string
  cashfree_order_id: string | null
  order_status?: string | null
  payment_session_id?: string | null
  payments: {
    cf_payment_id: string | null
    status: string
    amount: string | null
    method: string | null
    paid_at: string | null
  }[]
}

export function useLiveOrderPayment(orderId: string) {
  return useQuery({
    queryKey: ["cashfree-live", "order", orderId],
    queryFn: async () => {
      const response = await apiClient.get<ApiResponse<LiveOrderPayment>>(
        `/payments/cashfree/live/orders/${orderId}`
      )
      if (!response.data.data) throw new Error("No data returned.")
      return response.data.data
    },
    enabled: Boolean(orderId),
    retry: false,
  })
}

export function useLiveCreateCheckout(orderId: string) {
  return useMutation({
    mutationFn: async () => {
      const response = await apiClient.post<
        ApiResponse<{ payment_session_id: string | null; created: boolean; mode: string }>
      >(`/payments/cashfree/live/orders/${orderId}/checkout`)
      return response.data.data
    },
  })
}

// --- Live Shopify refunds, variant detail ----------------------------------------------

export interface LiveOrderRefunds {
  cancelled_at: string | null
  cancel_reason: string | null
  total_refunded: string | null
  currency: string | null
  refunds: {
    id: string
    created_at: string | null
    processed_at: string | null
    note: string | null
    amount: string | null
  }[]
}

export function useLiveOrderRefunds(orderId: string) {
  return useQuery({
    queryKey: ["shopify-live", "refunds", orderId],
    queryFn: async () => {
      const response = await apiClient.get<ApiResponse<LiveOrderRefunds>>(
        `/live/orders/${orderId}/refunds`
      )
      if (!response.data.data) throw new Error("No data returned.")
      return response.data.data
    },
    enabled: Boolean(orderId),
    retry: false,
  })
}

export interface LiveVariantDetail extends LiveCatalogVariant {
  external_product_id: string
  product_title: string
}

export function useLiveVariant(externalVariantId: string) {
  return useQuery({
    queryKey: ["catalog-live", "variant", externalVariantId],
    queryFn: async () => {
      const response = await apiClient.get<ApiResponse<LiveVariantDetail>>(
        `/live/catalog/variants/${externalVariantId}`
      )
      if (!response.data.data) throw new Error("No data returned.")
      return response.data.data
    },
    enabled: Boolean(externalVariantId),
    retry: false,
  })
}

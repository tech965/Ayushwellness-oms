import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import { apiClient } from "@/lib/api-client"
import type { ApiResponse, PaginatedResponse } from "@/types/api"
import type {
  PlatformStockMovement,
  PlatformStockMovementCreateInput,
  ProductMarketplaceMovement,
  ProductMarketplaceMovementCreateInput,
  ProductMarketplaceMovementEditInput,
  ProductMarketplaceMovementUndoInput,
  ProductPlatformStock,
  ProductShipmentSummary,
  UnifiedStockMovement,
} from "@/types/platform-inventory"

async function fetchProductPlatformStock(
  productId: string,
  stockDate: string
): Promise<ProductPlatformStock> {
  const response = await apiClient.get<ApiResponse<ProductPlatformStock>>(
    `/inventory/products/${productId}/platform-stock`,
    { params: { stock_date: stockDate } }
  )
  if (!response.data.data) throw new Error("Product not found.")
  return response.data.data
}

/** Marketplace Stock table: Shopify (automatic) + every manual platform,
 * for every real SKU under this product, as of `stockDate` (`YYYY-MM-DD`,
 * IST — see `lib/ist-date.ts::istDateKey`). Re-fetches whenever the date
 * changes, same as every other date-scoped query in this codebase — the
 * backend query genuinely uses the selected date, never just relabels
 * today's numbers.
 */
export function useProductPlatformStock(productId: string, stockDate: string) {
  return useQuery({
    queryKey: ["inventory", "products", productId, "platform-stock", stockDate],
    queryFn: () => fetchProductPlatformStock(productId, stockDate),
    enabled: Boolean(productId) && Boolean(stockDate),
  })
}

async function fetchProductShipmentSummary(
  productId: string,
  stockDate: string
): Promise<ProductShipmentSummary> {
  const response = await apiClient.get<ApiResponse<ProductShipmentSummary>>(
    `/inventory/products/${productId}/shipment-summary`,
    { params: { stock_date: stockDate } }
  )
  if (!response.data.data) throw new Error("Product not found.")
  return response.data.data
}

/** In Transit / Out for Delivery / Delivered (on date) / RTO, for every
 * real SKU under this product — derived from the existing Shiprocket-
 * driven shipment/dispatch data, never a fabricated count.
 */
export function useProductShipmentSummary(productId: string, stockDate: string) {
  return useQuery({
    queryKey: ["inventory", "products", productId, "shipment-summary", stockDate],
    queryFn: () => fetchProductShipmentSummary(productId, stockDate),
    enabled: Boolean(productId) && Boolean(stockDate),
  })
}

interface PlatformMovementHistoryParams {
  page: number
  pageSize: number
  platform?: string
  date_from?: string
  date_to?: string
}

async function fetchPlatformMovementHistory(
  variantId: string,
  params: PlatformMovementHistoryParams
): Promise<PaginatedResponse<UnifiedStockMovement>> {
  const response = await apiClient.get<PaginatedResponse<UnifiedStockMovement>>(
    `/inventory/stock/${variantId}/platform-stock/movements`,
    {
      params: {
        page: params.page,
        page_size: params.pageSize,
        platform: params.platform || undefined,
        date_from: params.date_from || undefined,
        date_to: params.date_to || undefined,
      },
    }
  )
  return response.data
}

/** Platform Stock Movement History: this variant's manual platform
 * movements interleaved with its existing Shopify movements, sorted by
 * time — a read-only, ADDITIONAL view. The existing standalone Shopify
 * movement history (`useInventoryMovements`) is untouched and still
 * queried separately by the page.
 */
export function usePlatformMovementHistory(
  variantId: string,
  params: PlatformMovementHistoryParams
) {
  return useQuery({
    queryKey: ["inventory", "stock", variantId, "platform-stock", "movements", params],
    queryFn: () => fetchPlatformMovementHistory(variantId, params),
    enabled: Boolean(variantId),
    placeholderData: (previous) => previous,
  })
}

/** Add Stock / Record Sale for one manual platform — the caller sends
 * ONLY the quantity being added or deducted, never the resulting total;
 * the backend computes and records the new balance from the actual last-
 * recorded balance (never trusts a client-supplied total), mirroring
 * `useAddVariantStock`'s existing contract exactly.
 */
export function useRecordPlatformStockMovement(variantId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (input: PlatformStockMovementCreateInput) => {
      const response = await apiClient.post<ApiResponse<PlatformStockMovement>>(
        `/inventory/stock/${variantId}/platform-stock/movements`,
        input
      )
      return response.data.data
    },
    onSuccess: () => {
      // Narrower than the existing blanket `invalidateInventory` helper
      // (`services/inventory.ts`) on purpose -- a platform-stock write
      // never changes Shopify's own numbers, so there's no reason to
      // refetch the (separate, potentially larger) Shopify stock/
      // movement queries too. The product-level summary is keyed by
      // `productId`, which this mutation (keyed by `variantId`) doesn't
      // know -- a `predicate` matching on "platform-stock" anywhere in
      // the key catches it (and the variant-level movement history)
      // without needing that id threaded through.
      void queryClient.invalidateQueries({
        predicate: (query) => query.queryKey.includes("platform-stock"),
      })
    },
  })
}

/** Record Sale / RTO -- for the whole PRODUCT on one platform, no SKU.
 * The caller sends only the packet quantity sold/returned, never the
 * resulting total; the backend converts to outers, computes the new
 * balances itself, and applies the same effect to the product's OMS
 * total stock in one transaction (see
 * `PlatformInventoryService.record_product_movement`).
 */
export function useRecordProductMarketplaceMovement(productId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (input: ProductMarketplaceMovementCreateInput) => {
      const response = await apiClient.post<ApiResponse<ProductMarketplaceMovement>>(
        `/inventory/products/${productId}/marketplace-movements`,
        input
      )
      return response.data.data
    },
    onSuccess: () => {
      // A Sale/RTO now also changes the product's OMS TOTAL stock (the
      // backend writes both in one transaction), so the product detail
      // header, its variant cards and the Inventory overview must refetch
      // too -- the displayed total always comes from the API, never from
      // a local subtraction.
      void queryClient.invalidateQueries({ queryKey: ["inventory"] })
    },
  })
}

interface ProductMarketplaceHistoryParams {
  page: number
  pageSize: number
  platform?: string
  /** Filter to ONE OMS-visible variant's own history (Gold / Red / Blue). */
  catalog_variant_id?: string
  date_from?: string
  date_to?: string
}

async function fetchProductMarketplaceHistory(
  productId: string,
  params: ProductMarketplaceHistoryParams
): Promise<PaginatedResponse<ProductMarketplaceMovement>> {
  const response = await apiClient.get<PaginatedResponse<ProductMarketplaceMovement>>(
    `/inventory/products/${productId}/marketplace-movements`,
    {
      params: {
        page: params.page,
        page_size: params.pageSize,
        platform: params.platform || undefined,
        catalog_variant_id: params.catalog_variant_id || undefined,
        date_from: params.date_from || undefined,
        date_to: params.date_to || undefined,
      },
    }
  )
  return response.data
}

/** Product-level Marketplace Adjustment history: Add Stock / Sale / RTO,
 * each its own event -- never merged or netted. Distinct from
 * `usePlatformMovementHistory` above (per-SKU, unaffected by this).
 */
export function useProductMarketplaceHistory(
  productId: string,
  params: ProductMarketplaceHistoryParams
) {
  return useQuery({
    queryKey: ["inventory", "products", productId, "marketplace-movements", params],
    queryFn: () => fetchProductMarketplaceHistory(productId, params),
    enabled: Boolean(productId),
    placeholderData: (previous) => previous,
  })
}

/** Correct a manual Sale/RTO's packet quantity. The original row is never
 * changed: the backend appends a reversal and a replacement in one
 * transaction, so both the marketplace balance and the OMS total reflect
 * only the new quantity. Refetches everything inventory-related (the OMS
 * total is computed by the backend, never locally).
 */
export function useEditMarketplaceMovement(movementId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (input: ProductMarketplaceMovementEditInput) => {
      const response = await apiClient.post<ApiResponse<ProductMarketplaceMovement>>(
        `/inventory/marketplace-movements/${movementId}/edit`,
        input
      )
      return response.data.data
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["inventory"] })
    },
  })
}

/** Undo a manual Sale/RTO by appending a reversal -- the original stays in
 * the history and the effective balance returns to its pre-movement value.
 */
export function useUndoMarketplaceMovement(movementId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (input: ProductMarketplaceMovementUndoInput) => {
      const response = await apiClient.post<ApiResponse<ProductMarketplaceMovement>>(
        `/inventory/marketplace-movements/${movementId}/undo`,
        input
      )
      return response.data.data
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["inventory"] })
    },
  })
}

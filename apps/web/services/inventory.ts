import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import { apiClient } from "@/lib/api-client"
import type { ApiResponse, PaginatedResponse } from "@/types/api"
import type {
  CatalogName,
  CatalogVariantStockAdjustment,
  InventoryMovement,
  InventoryMovementFilters,
  InventoryProductStock,
  InventoryProductSummary,
  InventoryProductVariants,
  InventoryStockFilters,
  InventoryVariant,
  OmsCatalogVariant,
} from "@/types/inventory"

interface ProductListParams extends InventoryStockFilters {
  page: number
  pageSize: number
}

async function fetchProducts(
  params: ProductListParams
): Promise<PaginatedResponse<InventoryProductSummary>> {
  const response = await apiClient.get<PaginatedResponse<InventoryProductSummary>>(
    "/inventory/stock",
    { params: { page: params.page, page_size: params.pageSize, q: params.q || undefined } }
  )
  return response.data
}

/** Main Inventory page: one row per PRODUCT (Product -> Variant ->
 * Inventory) -- never the flat per-SKU listing.
 */
export function useInventoryProducts(params: ProductListParams) {
  return useQuery({
    queryKey: ["inventory", "products", params],
    queryFn: () => fetchProducts(params),
    placeholderData: (previous) => previous,
  })
}

async function fetchProductVariants(productId: string): Promise<InventoryProductVariants> {
  const response = await apiClient.get<ApiResponse<InventoryProductVariants>>(
    `/inventory/products/${productId}/variants`
  )
  if (!response.data.data) throw new Error("Product not found.")
  return response.data.data
}

export function useInventoryProductVariants(productId: string) {
  return useQuery({
    queryKey: ["inventory", "products", productId, "variants"],
    queryFn: () => fetchProductVariants(productId),
    enabled: Boolean(productId),
  })
}

async function fetchProductStock(productId: string): Promise<InventoryProductStock> {
  const response = await apiClient.get<ApiResponse<InventoryProductStock>>(
    `/inventory/products/${productId}/stock`
  )
  if (!response.data.data) throw new Error("Product not found.")
  return response.data.data
}

/** The ONE product-level Inventory card (Product -> one card -> complete
 * stock). Aggregated server-side from the product's underlying variant
 * rows, which stay intact.
 */
export function useInventoryProductStock(productId: string) {
  return useQuery({
    queryKey: ["inventory", "products", productId, "stock"],
    queryFn: () => fetchProductStock(productId),
    enabled: Boolean(productId),
  })
}

interface MovementListParams extends InventoryMovementFilters {
  page: number
  pageSize: number
}

async function fetchMovements(
  params: MovementListParams
): Promise<PaginatedResponse<InventoryMovement>> {
  const response = await apiClient.get<PaginatedResponse<InventoryMovement>>(
    "/inventory/movements",
    {
      params: {
        page: params.page,
        page_size: params.pageSize,
        product_variant_id: params.product_variant_id,
        product_id: params.product_id,
        catalog_variant_id: params.catalog_variant_id,
        order_id: params.order_id,
        movement_type: params.movement_type,
      },
    }
  )
  return response.data
}

export function useInventoryMovements(params: MovementListParams) {
  return useQuery({
    queryKey: ["inventory", "movements", params],
    queryFn: () => fetchMovements(params),
    placeholderData: (previous) => previous,
  })
}

function invalidateInventory(queryClient: ReturnType<typeof useQueryClient>) {
  void queryClient.invalidateQueries({ queryKey: ["inventory"] })
}

/** Add-incoming-stock: the caller sends ONLY the quantity being added
 * (e.g. "+100 boxes"), never the resulting total -- the backend computes
 * and records the new balance (current + quantity_to_add), see
 * `InventoryService.add_stock`. Can only increase stock; the backend
 * rejects a non-positive quantity.
 */
export function useAddStock(variantId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (input: { quantity_to_add: number; reason: string }) => {
      const response = await apiClient.post<ApiResponse<InventoryMovement>>(
        `/inventory/stock/${variantId}/adjust`,
        input
      )
      return response.data.data
    },
    onSuccess: () => invalidateInventory(queryClient),
  })
}

/** Add-incoming-stock for a SINGLE-variant product -- the backend
 * forwards to that variant's addition. A multi-variant product is
 * rejected (422); use `useAddVariantStock` per row instead.
 */
export function useAddProductStock(productId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (input: { quantity_to_add: number; reason: string }) => {
      const response = await apiClient.post<ApiResponse<InventoryMovement>>(
        `/inventory/products/${productId}/adjust`,
        input
      )
      return response.data.data
    },
    onSuccess: () => invalidateInventory(queryClient),
  })
}

/** Variant-agnostic add-incoming-stock -- used for the one-row-per-
 * variant Add Stock on a multi-variant product card. Pass the variant id
 * per call so a single hook instance drives every row.
 */
export function useAddVariantStock() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (input: {
      variantId: string
      quantity_to_add: number
      reason: string
    }) => {
      const { variantId, ...body } = input
      const response = await apiClient.post<ApiResponse<InventoryMovement>>(
        `/inventory/stock/${variantId}/adjust`,
        body
      )
      return response.data.data
    },
    onSuccess: () => invalidateInventory(queryClient),
  })
}

/** Add-incoming-stock for a multi-SKU OMS-visible variant's TOTAL stock
 * (e.g. Blue Packet's combined 60/120/180) -- the caller sends ONE
 * quantity to add, never a per-SKU value. Recorded on the CatalogVariant's
 * own reconciliation ledger; no underlying `ProductVariant` row is ever
 * touched by this call (see `InventoryService.add_catalog_variant_stock`).
 */
export function useAddCatalogVariantStock() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (input: {
      catalogVariantId: string
      quantity_to_add: number
      reason: string
    }) => {
      const { catalogVariantId, ...body } = input
      const response = await apiClient.post<ApiResponse<CatalogVariantStockAdjustment>>(
        `/inventory/catalog-variants/${catalogVariantId}/adjust`,
        body
      )
      return response.data.data
    },
    onSuccess: () => invalidateInventory(queryClient),
  })
}

interface CatalogVariantAdjustmentListParams {
  page: number
  pageSize: number
}

async function fetchCatalogVariantAdjustments(
  catalogVariantId: string,
  params: CatalogVariantAdjustmentListParams
): Promise<PaginatedResponse<CatalogVariantStockAdjustment>> {
  const response = await apiClient.get<PaginatedResponse<CatalogVariantStockAdjustment>>(
    `/inventory/catalog-variants/${catalogVariantId}/adjustments`,
    { params: { page: params.page, page_size: params.pageSize } }
  )
  return response.data
}

/** History of Total-Stock edits for one OMS-visible variant -- shown
 * alongside, never merged into, its underlying SKUs' own dispatch/RTO/
 * manual movement history (`useInventoryMovements`), which this never
 * touches.
 */
export function useCatalogVariantAdjustments(
  catalogVariantId: string,
  params: CatalogVariantAdjustmentListParams
) {
  return useQuery({
    queryKey: ["inventory", "catalog-variants", catalogVariantId, "adjustments", params],
    queryFn: () => fetchCatalogVariantAdjustments(catalogVariantId, params),
    enabled: Boolean(catalogVariantId),
    placeholderData: (previous) => previous,
  })
}

/** Changes only the packets<->boxes conversion for this variant -- never
 * moves `available_boxes` itself.
 */
export function useUpdatePacketsPerBox(variantId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (input: { packets_per_box: number }) => {
      const response = await apiClient.patch<ApiResponse<InventoryVariant>>(
        `/inventory/stock/${variantId}/settings`,
        input
      )
      return response.data.data
    },
    onSuccess: () => invalidateInventory(queryClient),
  })
}

/** How many packets/pouches ONE unit of this variant, as ordered,
 * contains -- combined with packets-per-box, drives how many boxes a
 * future dispatch/RTO deducts/restores for this SKU. Never moves
 * `available_boxes` itself.
 */
export function useUpdatePackSize(variantId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (input: { pack_size: number }) => {
      const response = await apiClient.patch<ApiResponse<InventoryVariant>>(
        `/inventory/stock/${variantId}/pack-size`,
        input
      )
      return response.data.data
    },
    onSuccess: () => invalidateInventory(queryClient),
  })
}

/** Manual "Edit Name": set a custom display name, or pass `null` to reset
 * to the Shopify name. Presentation only -- the backend never touches the
 * real Shopify `title`, stock, or the movement ledger, and a later Shopify
 * product sync does not overwrite the custom name.
 */
export function useSetProductName(productId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (name: string | null) => {
      const url = `/inventory/products/${productId}/name`
      const response =
        name === null
          ? await apiClient.delete<ApiResponse<CatalogName>>(url)
          : await apiClient.patch<ApiResponse<CatalogName>>(url, { name })
      return response.data.data
    },
    onSuccess: () => invalidateInventory(queryClient),
  })
}

export function useSetVariantName(variantId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (name: string | null) => {
      const url = `/inventory/stock/${variantId}/name`
      const response =
        name === null
          ? await apiClient.delete<ApiResponse<CatalogName>>(url)
          : await apiClient.patch<ApiResponse<CatalogName>>(url, { name })
      return response.data.data
    },
    onSuccess: () => invalidateInventory(queryClient),
  })
}

/** Rename an OMS-visible catalog variant (e.g. "Ghutka Flavour"). No
 * underlying ProductVariant, stock, or ledger row is touched; Shopify
 * sync never reads or writes this name. Non-empty only (no reset).
 */
export function useSetCatalogVariantName(catalogVariantId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (name: string) => {
      const response = await apiClient.patch<ApiResponse<OmsCatalogVariant>>(
        `/inventory/catalog-variants/${catalogVariantId}/name`,
        { name }
      )
      return response.data.data
    },
    onSuccess: () => invalidateInventory(queryClient),
  })
}

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import { apiClient } from "@/lib/api-client"
import type { ApiResponse, PaginatedResponse } from "@/types/api"
import type {
  CatalogName,
  InventoryMovement,
  InventoryMovementFilters,
  InventoryProductStock,
  InventoryProductSummary,
  InventoryProductVariants,
  InventoryStockFilters,
  InventoryVariant,
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

/** Absolute-target adjustment: the caller sends the new total stock (e.g.
 * "25 boxes"), never a raw delta -- the backend computes and records the
 * delta (see `InventoryService.adjust_to_target`).
 */
export function useAdjustStock(variantId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (input: { target_boxes: number; reason: string }) => {
      const response = await apiClient.post<ApiResponse<InventoryMovement>>(
        `/inventory/stock/${variantId}/adjust`,
        input
      )
      return response.data.data
    },
    onSuccess: () => invalidateInventory(queryClient),
  })
}

/** Absolute-target edit for a SINGLE-variant product -- the backend
 * forwards to that variant's adjustment. A multi-variant product is
 * rejected (422); use `useAdjustVariantStock` per row instead.
 */
export function useAdjustProductStock(productId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (input: { target_boxes: number; reason: string }) => {
      const response = await apiClient.post<ApiResponse<InventoryMovement>>(
        `/inventory/products/${productId}/adjust`,
        input
      )
      return response.data.data
    },
    onSuccess: () => invalidateInventory(queryClient),
  })
}

/** Variant-agnostic absolute-target edit -- used for the one-row-per-
 * variant Edit Stock on a multi-variant product card. Pass the variant id
 * per call so a single hook instance drives every row.
 */
export function useAdjustVariantStock() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (input: {
      variantId: string
      target_boxes: number
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

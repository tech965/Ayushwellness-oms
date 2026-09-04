import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import { apiClient } from "@/lib/api-client"
import type { ApiResponse, PaginatedResponse } from "@/types/api"
import type {
  InventoryMovement,
  InventoryMovementFilters,
  InventoryStock,
  InventoryStockFilters,
} from "@/types/inventory"

interface StockListParams extends InventoryStockFilters {
  page: number
  pageSize: number
}

async function fetchStock(params: StockListParams): Promise<PaginatedResponse<InventoryStock>> {
  const response = await apiClient.get<PaginatedResponse<InventoryStock>>("/inventory/stock", {
    params: {
      page: params.page,
      page_size: params.pageSize,
      q: params.q || undefined,
      low_stock_only: params.low_stock_only || undefined,
    },
  })
  return response.data
}

export function useInventoryStock(params: StockListParams) {
  return useQuery({
    queryKey: ["inventory", "stock", params],
    queryFn: () => fetchStock(params),
    placeholderData: (previous) => previous,
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

export function useAdjustStock(variantId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (input: { delta: number; reason: string }) => {
      const response = await apiClient.post<ApiResponse<InventoryMovement>>(
        `/inventory/stock/${variantId}/adjust`,
        input
      )
      return response.data.data
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["inventory"] })
    },
  })
}

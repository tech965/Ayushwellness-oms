import { useQuery } from "@tanstack/react-query"

import { apiClient } from "@/lib/api-client"
import type { ApiResponse, PaginatedResponse } from "@/types/api"
import type { Product, ProductDetail, ProductListFilters } from "@/types/product"

interface ListParams extends ProductListFilters {
  page: number
  pageSize: number
}

async function fetchProducts(params: ListParams): Promise<PaginatedResponse<Product>> {
  const response = await apiClient.get<PaginatedResponse<Product>>("/products", {
    params: {
      page: params.page,
      page_size: params.pageSize,
      q: params.q || undefined,
      status: params.status,
    },
  })
  return response.data
}

export function useProducts(params: ListParams) {
  return useQuery({
    queryKey: ["products", params],
    queryFn: () => fetchProducts(params),
    placeholderData: (previous) => previous,
  })
}

async function fetchProduct(id: string): Promise<ProductDetail> {
  const response = await apiClient.get<ApiResponse<ProductDetail>>(`/products/${id}`)
  if (!response.data.data) throw new Error("Product not found.")
  return response.data.data
}

export function useProduct(id: string) {
  return useQuery({
    queryKey: ["products", id],
    queryFn: () => fetchProduct(id),
    enabled: Boolean(id),
  })
}

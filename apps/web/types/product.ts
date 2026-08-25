// Mirrors app/schemas/product.py

export type ProductStatus = "active" | "draft" | "archived"

export interface ProductVariant {
  id: string
  product_id: string
  shopify_variant_id: string | null
  sku: string
  title: string | null
  price: string
  compare_at_price: string | null
  inventory_quantity: number
  weight: string | null
  barcode: string | null
  options: Record<string, string> | null
  status: ProductStatus
  created_at: string
  updated_at: string
}

export interface Product {
  id: string
  shopify_product_id: string | null
  title: string
  description: string | null
  status: ProductStatus
  vendor: string | null
  product_type: string | null
  tags: string | null
  source_system: string | null
  created_at: string
  updated_at: string
}

export interface ProductDetail extends Product {
  variants: ProductVariant[]
}

export interface ProductListFilters {
  q?: string
  status?: ProductStatus
}

export const PRODUCT_STATUS_OPTIONS: { label: string; value: ProductStatus }[] = [
  { label: "Active", value: "active" },
  { label: "Draft", value: "draft" },
  { label: "Archived", value: "archived" },
]

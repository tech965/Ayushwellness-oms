// Mirrors app/schemas/inventory.py -- Product -> Variant -> Inventory,
// everything in BOXES. `shopify_inventory_quantity` is a passive reference
// value only; it never drives any stock calculation.

export type StockStatus = "in_stock" | "low_stock" | "out_of_stock"

export type InventoryMovementType =
  | "dispatch"
  | "rto_restock"
  | "manual_adjustment"
  | "initial_stock"

export interface InventoryProductSummary {
  id: string
  title: string
  vendor: string | null
  variant_count: number
  total_available_boxes: number
  total_packets: number
  stock_status: StockStatus
  updated_at: string
}

export interface InventoryVariant {
  id: string
  product_id: string
  sku: string
  variant_title: string | null
  packets_per_box: number
  available_boxes: number
  total_packets: number
  stock_status: StockStatus
  status: "active" | "draft" | "archived"
  shopify_inventory_quantity: number
  updated_at: string
}

export interface InventoryProductVariants {
  product_id: string
  product_title: string
  variants: InventoryVariant[]
}

export interface InventoryMovement {
  id: string
  product_variant_id: string
  product_id: string | null
  product_title: string | null
  variant_title: string | null
  sku: string | null
  movement_type: InventoryMovementType
  quantity_delta: number
  previous_balance: number
  quantity_after: number
  order_id: string | null
  shipment_id: string | null
  rto_id: string | null
  actor_user_id: string | null
  actor_label: string
  reason: string | null
  notes: string | null
  created_at: string
}

export interface InventoryStockFilters {
  q?: string
}

export interface InventoryMovementFilters {
  product_variant_id?: string
  product_id?: string
  order_id?: string
  movement_type?: InventoryMovementType
}

export const STOCK_STATUS_LABELS: Record<StockStatus, string> = {
  in_stock: "In Stock",
  low_stock: "Low Stock",
  out_of_stock: "Out of Stock",
}

export const STOCK_STATUS_BADGE_CLASSES: Record<StockStatus, string> = {
  in_stock: "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300",
  low_stock: "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300",
  out_of_stock: "bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-300",
}

export const INVENTORY_MOVEMENT_TYPE_OPTIONS: { label: string; value: InventoryMovementType }[] = [
  { label: "Dispatch", value: "dispatch" },
  { label: "RTO restock", value: "rto_restock" },
  { label: "Manual adjustment", value: "manual_adjustment" },
  { label: "Initial stock", value: "initial_stock" },
]

// Mirrors app/schemas/inventory.py

export type InventoryMovementType =
  | "dispatch"
  | "rto_restock"
  | "manual_adjustment"
  | "initial_stock"

export interface InventoryStock {
  id: string
  product_id: string
  sku: string
  variant_title: string | null
  product_title: string
  available_quantity: number
  inventory_quantity: number
  status: "active" | "draft" | "archived"
  updated_at: string
}

export interface InventoryMovement {
  id: string
  product_variant_id: string
  sku: string | null
  movement_type: InventoryMovementType
  quantity_delta: number
  quantity_after: number
  order_id: string | null
  shipment_id: string | null
  rto_id: string | null
  actor_user_id: string | null
  reason: string | null
  notes: string | null
  created_at: string
}

export interface InventoryStockFilters {
  q?: string
  low_stock_only?: boolean
}

export interface InventoryMovementFilters {
  product_variant_id?: string
  order_id?: string
  movement_type?: InventoryMovementType
}

export const INVENTORY_MOVEMENT_TYPE_OPTIONS: { label: string; value: InventoryMovementType }[] = [
  { label: "Dispatch", value: "dispatch" },
  { label: "RTO restock", value: "rto_restock" },
  { label: "Manual adjustment", value: "manual_adjustment" },
  { label: "Initial stock", value: "initial_stock" },
]

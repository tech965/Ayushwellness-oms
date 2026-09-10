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
  /** Raw Shopify name. */
  title: string
  /** Staff-set custom name, or null. */
  title_override: string | null
  /** What the UI shows: `title_override` if set, else `title`. */
  display_title: string
  vendor: string | null
  /** Shopify's featured product image, or null. Source of truth is
   * Shopify -- rendered directly, never re-hosted by the OMS.
   */
  image_url: string | null
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
  /** Raw Shopify name. */
  variant_title: string | null
  /** Staff-set custom name, or null. */
  variant_title_override: string | null
  /** What the UI shows: override if set, else Shopify title, else SKU. */
  display_title: string
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
  product_title_override: string | null
  product_display_title: string
  variants: InventoryVariant[]
}

/** One underlying Shopify variant row inside an OMS-visible variant. */
export interface ProductVariantStockLine {
  id: string
  sku: string
  variant_title: string | null
  variant_title_override: string | null
  display_title: string
  available_boxes: number
  packets_per_box: number
  total_packets: number
  stock_status: StockStatus
  /** This row's OWN Shopify image, if Shopify assigned one distinct from
   * the product's featured image. Usually null.
   */
  image_url: string | null
}

/** ONE OMS-visible variant on the product detail page. Groups one or more
 * underlying Shopify `ProductVariant` rows. `catalog_variant_id` is null
 * for an implicit (not-yet-grouped) OMS variant that maps 1:1 to its
 * single underlying row.
 */
export interface OmsCatalogVariant {
  catalog_variant_id: string | null
  name: string
  display_order: number
  is_active: boolean
  available_boxes: number
  total_packets: number
  stock_status: StockStatus
  packets_per_box_uniform: boolean
  underlying_variant_count: number
  underlying_variants: ProductVariantStockLine[]
  /** Resolved server-side: an underlying variant's own image if Shopify
   * gave one a distinct photo, else the product's featured image, else
   * null. Never guessed client-side.
   */
  image_url: string | null
}

/** Product detail payload. `oms_variants` is the ONLY variant view shown
 * (3 for Aayush Herbal Masala, 1 for every other grouped product). The
 * raw Shopify rows live only inside each OMS variant's
 * `underlying_variants`.
 */
export interface InventoryProductStock {
  product_id: string
  /** Shopify's own, immutable product id -- the only safe key to scope
   * any product-specific display behaviour by (never `product_id`, the
   * per-environment OMS UUID, and never `title`/`product_name`, which
   * two distinct Shopify products can share).
   */
  shopify_product_id: string | null
  product_name: string
  title: string
  title_override: string | null
  /** Shopify's featured product image, or null. */
  image_url: string | null
  available_boxes: number
  total_packets: number
  stock_status: StockStatus
  packets_per_box_uniform: boolean
  oms_variant_count: number
  underlying_variant_count: number
  oms_variants: OmsCatalogVariant[]
}

export interface CatalogName {
  id: string
  title: string | null
  title_override: string | null
  display_title: string
}

export interface InventoryMovement {
  id: string
  product_variant_id: string
  product_id: string | null
  product_title: string | null
  variant_title: string | null
  variant_display_title: string | null
  /** OMS-visible variant this movement's Shopify SKU is grouped under. */
  catalog_variant_id: string | null
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
  catalog_variant_id?: string
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
  { label: "Shipped", value: "dispatch" },
  { label: "RTO Delivered", value: "rto_restock" },
  { label: "Manual adjustment", value: "manual_adjustment" },
  { label: "Initial stock", value: "initial_stock" },
]

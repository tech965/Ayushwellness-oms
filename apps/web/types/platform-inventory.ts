/** Mirrors apps/api/app/schemas/platform_inventory.py and
 * apps/api/app/models/platform_inventory.py::InventoryPlatform.
 * All quantities are in BOXES, same unit as types/inventory.ts.
 */

export type ManualPlatform = "amazon" | "flipkart" | "blinkit" | "meesho" | "manual_other"

/** Every value the "Add Stock" / "Record Sale" dialog's Platform field
 * can submit — deliberately a plain string union mirroring the backend's
 * `InventoryPlatform.ALL` (never a hardcoded switch scattered through the
 * app) so adding a platform later is a one-line change here, not a
 * rewrite.
 */
export const MANUAL_PLATFORMS: ManualPlatform[] = [
  "amazon",
  "flipkart",
  "blinkit",
  "meesho",
  "manual_other",
]

export const PLATFORM_LABELS: Record<ManualPlatform | "shopify", string> = {
  shopify: "Shopify",
  amazon: "Amazon",
  flipkart: "Flipkart",
  blinkit: "Blinkit",
  meesho: "Meesho",
  manual_other: "Manual / Other",
}

export type PlatformStockMovementType = "stock_added" | "stock_deducted"

export interface PlatformStockMovementCreateInput {
  platform: ManualPlatform
  movement_type: PlatformStockMovementType
  quantity: number
  reason?: string
  /** `YYYY-MM-DD` (IST). Omit to default to today (IST) on the backend. */
  stock_date?: string
}

export interface PlatformStockMovement {
  id: string
  product_variant_id: string
  platform: string
  platform_label: string
  movement_type: PlatformStockMovementType
  quantity_delta: number
  quantity_after: number
  stock_date: string
  reason: string | null
  actor_user_id: string | null
  actor_label: string
  created_at: string
}

/** One row of the merged "Platform Stock Movement History" table — this
 * variant's manual platform movements interleaved with its existing
 * Shopify movements (`dispatch`/`rto_restock`/`manual_adjustment`/
 * `initial_stock`), sorted by time.
 */
export interface UnifiedStockMovement {
  id: string
  platform: string
  platform_label: string
  movement_type: string
  quantity_delta: number
  quantity_after: number
  stock_date: string
  reason: string | null
  actor_label: string
  created_at: string
}

/** One platform's row in the "Marketplace Stock" table, for one product
 * variant, on the selected stock date.
 *
 * `is_automatic=true` (Shopify) only: `opening_stock` is always `null`
 * and `current_stock` is always the LIVE current value, regardless of
 * `stock_date` — Shopify has no historical day-by-day balance snapshot,
 * so a past date's balance is never fabricated (only its real
 * `stock_added`/`stock_deducted` movement totals for that date are
 * shown). Never treat a `null` `opening_stock` as "0" in the UI — it
 * means "not applicable", not "zero".
 */
export interface PlatformStockSummaryRow {
  platform: string
  platform_label: string
  is_automatic: boolean
  opening_stock: number | null
  stock_added: number
  stock_deducted: number
  current_stock: number
  last_updated: string | null
}

export interface VariantPlatformStock {
  product_variant_id: string
  sku: string
  variant_title: string | null
  stock_date: string
  platforms: PlatformStockSummaryRow[]
}

export interface ProductPlatformStock {
  product_id: string
  product_title: string
  stock_date: string
  variants: VariantPlatformStock[]
}

/** One variant's shipment-status breakdown — `in_transit`/
 * `out_for_delivery`/`rto` are LIVE current counts (shipment status has
 * no historical day-by-day record), never scoped to the selected date;
 * only `delivered_on_date` is genuinely date-scoped. Never add
 * `in_transit` into `current_stock` — it's stock already dispatched, not
 * available marketplace stock (Requirement 16).
 */
export interface ShipmentTransitSummaryRow {
  product_variant_id: string
  sku: string
  in_transit: number
  out_for_delivery: number
  delivered_on_date: number
  rto: number
}

export interface ProductShipmentSummary {
  product_id: string
  stock_date: string
  in_transit: number
  out_for_delivery: number
  delivered_on_date: number
  rto: number
  variants: ShipmentTransitSummaryRow[]
}

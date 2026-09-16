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
 * `is_automatic=true` (Shopify): `current_stock` is the true LIVE value
 * only when `stock_date` is TODAY. For a PAST date, `current_stock`/
 * `opening_stock` are reconstructed from Shopify's own existing movement
 * ledger (never fabricated) and are `null` — never `0` — when that
 * ledger has no row before the relevant cutoff (it may predate the
 * variant's first-ever movement). Render `null` as "historical data not
 * available" (e.g. "—"), never as zero — a `null` reading must never be
 * mistaken for "no stock".
 *
 * `is_automatic=false` (manual platforms): `current_stock` defaults to
 * `0` (never `null`) when nothing has ever been recorded — unlike
 * Shopify, this ledger IS the only source of truth for a manual
 * platform, so "nothing recorded" legitimately means 0.
 */
export interface PlatformStockSummaryRow {
  platform: string
  platform_label: string
  is_automatic: boolean
  opening_stock: number | null
  stock_added: number
  stock_deducted: number
  current_stock: number | null
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

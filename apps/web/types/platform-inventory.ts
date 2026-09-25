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

/** ONE OMS-visible variant's own Marketplace Stock (a product with two or
 * more CatalogVariants -- Herbal Masala's Gold/Red/Blue). Its Shopify row
 * is summed across only THAT variant's SKUs; its manual rows come from
 * movements scoped to it, so Amazon Gold never mixes with Amazon Red. The
 * 60/120/180 SKUs never get a table of their own.
 */
export interface VariantMarketplaceStock {
  catalog_variant_id: string
  name: string
  display_order: number
  platforms: PlatformStockSummaryRow[]
  /** Effective packets sold by THIS variant this IST calendar month
   * (Sale only; a reversed Sale is excluded).
   */
  sold_this_month_packets: number
}

/** Marketplace Stock, in one of two shapes:
 *  - `scope: "product"` -- ONE table for the product in `platforms`
 *    (Shopify already summed across SKUs server-side).
 *  - `scope: "catalog_variant"` -- one table PER OMS-visible variant in
 *    `variants` (`platforms` is empty); never combined across variants.
 */
export interface ProductPlatformStock {
  product_id: string
  product_title: string
  stock_date: string
  scope: "product" | "catalog_variant"
  platforms: PlatformStockSummaryRow[]
  variants: VariantMarketplaceStock[]
  /** Packets sold across every manual platform in the CURRENT IST
   * calendar month for a `scope: "product"` product -- effective Sale
   * movements only, computed by the backend from real records (never
   * RTO, never Shopify's automatic movements). 0 for a variant-scoped
   * product, which reports it per variant instead.
   */
  sold_this_month_packets: number
}

/** A marketplace movement's type. `stock_added` is legacy only (no UI or
 * API path creates it any more); `reversal` is a compensating row written
 * by Undo / Edit (never entered directly).
 */
export type ProductMarketplaceMovementType = "stock_added" | "sale" | "rto" | "reversal"

/** What the Record Sale / RTO dialogs may submit -- no SKU. `quantity_packets`
 * is what the business user actually typed; the backend converts it to
 * outers using the in-scope SKUs' pack_size/packets_per_box (422 if those
 * aren't uniform) and applies the same effect to the OMS total stock in
 * one transaction.
 */
export type MarketplaceOperation = "sale" | "rto"

export interface ProductMarketplaceMovementCreateInput {
  platform: ManualPlatform
  movement_type: MarketplaceOperation
  quantity_packets: number
  /** ONLY for a variant-scoped product (Herbal): the OMS-visible variant
   * (Gold/Red/Blue) the movement belongs to -- never an underlying SKU.
   */
  catalog_variant_id?: string
  reason?: string
  /** `YYYY-MM-DD` (IST). Omit to default to today (IST) on the backend. */
  stock_date?: string
}

/** Edit appends a reversal + a replacement; Undo appends a reversal. The
 * original row is never changed. A reason is required for the audit trail.
 */
export interface ProductMarketplaceMovementEditInput {
  quantity_packets: number
  reason: string
}

export interface ProductMarketplaceMovementUndoInput {
  reason: string
}

/** Effective state derived from the reversal/replacement links:
 *  active -- in force; undone -- reversed; edited -- reversed and
 *  replaced; reversal -- a compensating row.
 */
export type MarketplaceMovementStatus = "active" | "undone" | "edited" | "reversal"

/** One row of the Marketplace history -- Sale / RTO / Reversal, each its
 * own event (never merged or netted).
 */
export interface ProductMarketplaceMovement {
  id: string
  product_id: string
  catalog_variant_id: string | null
  platform: string
  platform_label: string
  movement_type: ProductMarketplaceMovementType
  quantity_packets: number
  quantity_delta: number
  quantity_after: number
  stock_date: string
  reason: string | null
  actor_user_id: string | null
  actor_label: string
  created_at: string
  reverses_movement_id: string | null
  replaces_movement_id: string | null
  /** On a replacement row: the quantity it replaced ("edited from 20"). */
  edited_from_packets: number | null
  status: MarketplaceMovementStatus
  can_edit: boolean
  can_undo: boolean
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

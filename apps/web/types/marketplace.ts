/** Mirrors apps/api/app/models/mixins.py::SourceSystem's marketplace
 * values. Marketplace Orders module, Phase 1.
 */

import type { KPIValue } from "./analytics"
import type {
  FulfillmentStatus,
  Order,
  OrderStatus,
  PaymentStatus,
} from "./order"

export type Marketplace = "amazon" | "flipkart" | "meesho"

/** `GET /marketplace/orders` rows -- `Order` plus the two fields that
 * table needs and the general Orders table doesn't (`app.schemas.order.
 * MarketplaceOrderListResponse`). `source_system` is already on `Order`.
 */
export interface MarketplaceOrder extends Order {
  external_id: string | null
  sku_summary: string | null
}

export interface MarketplaceOrderFilters {
  marketplace?: Marketplace
  status?: OrderStatus
  payment_status?: PaymentStatus
  fulfillment_status?: FulfillmentStatus
  shipment_status?: string
  sku?: string
  date_from?: string
  date_to?: string
  q?: string
}

export const MARKETPLACE_OPTIONS: { label: string; value: Marketplace }[] = [
  { label: "Amazon", value: "amazon" },
  { label: "Flipkart", value: "flipkart" },
  { label: "Meesho", value: "meesho" },
]

export const MARKETPLACE_LABELS: Record<string, string> = {
  amazon: "Amazon",
  flipkart: "Flipkart",
  meesho: "Meesho",
}

export function marketplaceLabel(sourceSystem: string | null | undefined): string {
  if (!sourceSystem) return "—"
  return MARKETPLACE_LABELS[sourceSystem] ?? sourceSystem
}

/** `GET /marketplace/dashboard/summary` -- same current-vs-previous-
 * period KPI shape as `AnalyticsSummary` (types/analytics.ts), scoped to
 * Amazon/Flipkart/Meesho only.
 */
export interface MarketplaceDashboardSummary {
  date_from: string
  date_to: string
  total_orders: KPIValue
  amazon_orders: KPIValue
  flipkart_orders: KPIValue
  meesho_orders: KPIValue
  total_sales: KPIValue
  pending_orders: KPIValue
  unfulfilled_orders: KPIValue
  shipped_orders: KPIValue
  delivered_orders: KPIValue
  cancelled_orders: KPIValue
  cod_orders: KPIValue
  prepaid_orders: KPIValue
}

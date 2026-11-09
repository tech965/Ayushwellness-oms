export interface AmazonStatus {
  configured: boolean
  missing: string[]
  region: string | null
  marketplace_id: string | null
  max_range_days: number
  last_successful_fetch_at: string | null
}

export interface ChunkRef {
  report_id: string
  start: string
  end: string
}

/** Money fields arrive as decimal strings; `null` means Amazon did not
 * provide the metric (shown as "Unavailable", never as 0). */
export interface DailyPoint {
  date: string
  units_ordered: number
  ordered_revenue: string
  units_shipped: number | null
  shipped_revenue: string | null
  units_refunded: number | null
}

export interface PeriodPoint {
  period: string
  label: string
  units_ordered: number
  ordered_revenue: string
  units_shipped: number | null
  shipped_revenue: string | null
  units_refunded: number | null
}

export interface ProductRow {
  asin: string
  sku: string | null
  product_name: string | null
  units_ordered: number
  units_shipped: number | null
  ordered_revenue: string
  avg_selling_price: string | null
}

export interface SalesReport {
  date_from: string
  date_to: string
  fetched_at: string
  summary: {
    currency: string | null
    total_units_ordered: number
    total_ordered_revenue: string
    products_sold: number
    total_units_shipped: number | null
    total_shipped_revenue: string | null
    total_units_refunded: number | null
  }
  daily: DailyPoint[]
  weekly: PeriodPoint[]
  monthly: PeriodPoint[]
  products: ProductRow[]
  unavailable: Record<string, string>
  warnings: string[]
  definitions: Record<string, string>
}

export interface PollResponse {
  state: "processing" | "done"
  chunk_statuses: Record<string, string>
  report: SalesReport | null
}

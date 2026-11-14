export interface FlipkartStatus {
  configured: boolean
  missing: string[]
  last_successful_fetch_at: string | null
}

/** `payment_type`/`tracking_id` are always `null` -- neither field exists
 * on Flipkart's Search Shipments API response (verified against the
 * official docs), so they're never fabricated here. See
 * `FlipkartOrdersPage.unavailable` for why. */
export interface FlipkartOrderItem {
  order_id: string
  shipment_id: string
  order_date: string
  status: string
  product_name: string | null
  sku: string | null
  fsn: string | null
  quantity: number
  selling_price: string | null
  payment_type: null
  tracking_id: null
}

export interface FlipkartOrdersPage {
  date_from: string | null
  date_to: string | null
  fetched_at: string
  items: FlipkartOrderItem[]
  next_page_url: string | null
  has_more: boolean
  unavailable: Record<string, string>
  warnings: string[]
}

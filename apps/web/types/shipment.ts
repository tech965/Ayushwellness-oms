// Mirrors app/schemas/shipment.py

export type ShipmentStatus =
  | "pending"
  | "picked_up"
  | "in_transit"
  | "out_for_delivery"
  | "delivered"
  | "ndr"
  | "rto_initiated"
  | "rto_delivered"
  | "cancelled"

export type ShipmentDelayStatus = "on_time" | "at_risk" | "delayed" | "unknown"

/** Outbound OMS -> Shopify fulfillment push state (Phase 6) — distinct
 * from `ShipmentStatus` (Shiprocket/logistics) and from an order's own
 * `fulfillment_status` (Shopify's inbound summary).
 */
export type ShopifySyncStatus = "not_applicable" | "pending" | "synced" | "failed"

export interface Shipment {
  id: string
  order_id: string
  shiprocket_shipment_id: string | null
  awb: string | null
  courier_id: string | null
  current_status: ShipmentStatus
  delay_status: ShipmentDelayStatus
  ndr_status: string | null
  rto_status: string | null
  pickup_date: string | null
  expected_delivery_date: string | null
  actual_delivery_date: string | null
  current_location: string | null
  last_tracking_update_at: string | null
  source_system: string | null
  shopify_fulfillment_id: string | null
  shopify_sync_status: ShopifySyncStatus
  shopify_sync_error: string | null
  shopify_synced_at: string | null
  created_at: string
  updated_at: string
  // Present only on rows from `GET /shipments` (`ShipmentListResponse`)
  // -- see `_to_shipment_list_response` in the backend endpoint. Absent
  // (undefined) on a plain `ShipmentResponse`, e.g. `GET /shipments/{id}`.
  order_number?: string | null
  customer_name?: string | null
  payment_type?: string | null
  confirmed_by_telecaller_name?: string | null
  courier_name?: string | null
}

export interface ShipmentEvent {
  id: string
  shipment_id: string
  external_event_id: string | null
  status: string
  location: string | null
  event_timestamp: string
  description: string | null
  courier_name: string | null
  source: string
  created_at: string
}

export interface ShipmentListFilters {
  q?: string
  status?: ShipmentStatus
  courier_id?: string
  date_from?: string
  date_to?: string
}

export const SHIPMENT_STATUS_OPTIONS: { label: string; value: ShipmentStatus }[] = [
  { label: "Pending", value: "pending" },
  { label: "Picked up", value: "picked_up" },
  { label: "In transit", value: "in_transit" },
  { label: "Out for delivery", value: "out_for_delivery" },
  { label: "Delivered", value: "delivered" },
  { label: "NDR", value: "ndr" },
  { label: "RTO initiated", value: "rto_initiated" },
  { label: "RTO delivered", value: "rto_delivered" },
  { label: "Cancelled", value: "cancelled" },
]

/** One row of the Shipment Queue — confirmed orders awaiting shipment
 * processing, a distinct/narrower view from the plain `Shipment` list
 * above (already-processed shipments). `shipment_*` fields are `null`
 * when no shipment has been created for this order yet.
 */
export interface ShipmentQueueRow {
  order_id: string
  order_number: string
  customer_name: string | null
  customer_phone: string | null
  item_summary: string | null
  sku_summary: string | null
  total_quantity: number
  total_amount: string
  payment_type: string
  payment_status: string
  confirmed_at: string | null
  confirmed_by_telecaller_id: string | null
  confirmed_by_telecaller_name: string | null
  shipment_id: string | null
  shipment_status: ShipmentStatus | null
  awb: string | null
  courier_name: string | null
}

export interface ShipmentQueueFilters {
  q?: string
  payment_type?: string
  telecaller_id?: string
  courier_id?: string
  sku?: string
  shipment_status?: ShipmentStatus
  date_from?: string
  date_to?: string
}

export interface ShipmentSummary {
  confirmed_awaiting_shipment: number
  total_shipments: number
  pending: number
  picked_up: number
  in_transit: number
  out_for_delivery: number
  delivered: number
  ndr: number
  rto: number
  cancelled: number
  cod: number
  prepaid: number
  todays_shipments: number
}

export interface ShipmentStatusBreakdownItem {
  status: string
  count: number
}

export interface DailyShipmentTrendPoint {
  date: string
  created: number
  delivered: number
}

export interface TelecallerShipmentStats {
  telecaller_id: string
  telecaller_name: string
  confirmed: number
  shipped: number
  delivered: number
  ndr: number
  rto: number
}

export interface ShipmentAnalytics {
  status_breakdown: ShipmentStatusBreakdownItem[]
  confirmation_to_shipment_rate: number
  daily_trend: DailyShipmentTrendPoint[]
  telecaller_stats: TelecallerShipmentStats[]
}

export interface BulkShipOrderResult {
  order_id: string
  success: boolean
  message: string | null
  shipment_id: string | null
}

export interface BulkShipOrdersResponse {
  shipped_count: number
  failed_count: number
  results: BulkShipOrderResult[]
}

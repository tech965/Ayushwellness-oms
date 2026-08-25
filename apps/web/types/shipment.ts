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
  created_at: string
  updated_at: string
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

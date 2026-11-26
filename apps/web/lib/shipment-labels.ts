import type { StatusTone } from "@/lib/status-styles"
import {
  UNIFIED_SHIPMENT_STATUS_OPTIONS,
  type StatusGroupKey,
  type UnifiedShipmentStatus,
} from "@/types/shipment"

/** Management-friendly shipment categories: label, chart colour and badge
 * tone. The grouping itself is defined once by the backend
 * (`status_groups`); this only decorates it, so the two cannot disagree on
 * which statuses a category holds.
 */
export const STATUS_GROUP_META: Record<
  StatusGroupKey,
  { label: string; color: string; tone: StatusTone }
> = {
  new_orders: { label: "New Orders", color: "#94a3b8", tone: "neutral" },
  ready_for_shipping: { label: "Ready for Shipping", color: "#60a5fa", tone: "info" },
  awaiting_pickup: {
    label: "Awaiting Courier Pickup",
    color: "#f59e0b",
    tone: "warning",
  },
  picked_up: { label: "Picked Up by Courier", color: "#38bdf8", tone: "info" },
  in_transit: { label: "In Transit", color: "#6366f1", tone: "info" },
  out_for_delivery: { label: "Out for Delivery", color: "#a855f7", tone: "purple" },
  delivered: { label: "Successfully Delivered", color: "#65a30d", tone: "success" },
  return_to_origin: { label: "Return to Origin", color: "#f97316", tone: "orange" },
  delivery_issue: { label: "Delivery Issue", color: "#ef4444", tone: "danger" },
  cancelled: { label: "Cancelled Orders", color: "#6b7280", tone: "neutral" },
  tracking_pending: {
    label: "Fulfilled – Tracking Pending",
    color: "#14b8a6",
    tone: "neutral",
  },
}

const GROUP_OF_STATUS: Record<UnifiedShipmentStatus, StatusGroupKey> = {
  order_placed: "new_orders",
  ready_to_ship: "ready_for_shipping",
  pickup_pending: "awaiting_pickup",
  picked_up: "picked_up",
  in_transit: "in_transit",
  rto_in_transit: "in_transit",
  out_for_delivery: "out_for_delivery",
  delivered: "delivered",
  rto_initiated: "return_to_origin",
  rto_delivered: "return_to_origin",
  ndr: "delivery_issue",
  cancelled: "cancelled",
  fulfilled_tracking_unavailable: "tracking_pending",
}

export function groupOfStatus(status: UnifiedShipmentStatus): StatusGroupKey {
  return GROUP_OF_STATUS[status]
}

/** Friendly category label for one technical status. */
export function friendlyStatusLabel(status: UnifiedShipmentStatus): string {
  return STATUS_GROUP_META[GROUP_OF_STATUS[status]]?.label ?? status
}

/** The original technical status, exactly as the old table named it. */
export function technicalStatusLabel(status: string): string {
  return (
    UNIFIED_SHIPMENT_STATUS_OPTIONS.find((o) => o.value === status)?.label ??
    status
      .split("_")
      .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
      .join(" ")
  )
}

export function paymentLabel(value: string): string {
  return value === "cod" ? "COD" : value === "prepaid" ? "Prepaid" : value
}

/** `null` renders as an em dash -- an unknown value is never shown as 0. */
export function formatDays(value: number | null | undefined): string {
  return value === null || value === undefined ? "—" : `${value.toFixed(1)} days`
}

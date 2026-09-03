export type StatusTone =
  "neutral" | "info" | "success" | "warning" | "danger" | "purple" | "orange"

export const STATUS_TONE_CLASSES: Record<StatusTone, string> = {
  neutral: "bg-muted text-muted-foreground",
  info: "bg-blue-100 text-blue-800 dark:bg-blue-500/15 dark:text-blue-400",
  success: "bg-lime-100 text-lime-800 dark:bg-lime-500/15 dark:text-lime-300",
  warning: "bg-amber-100 text-amber-800 dark:bg-amber-500/15 dark:text-amber-400",
  danger: "bg-red-100 text-red-800 dark:bg-red-500/15 dark:text-red-400",
  purple: "bg-purple-100 text-purple-800 dark:bg-purple-500/15 dark:text-purple-400",
  orange: "bg-orange-100 text-orange-800 dark:bg-orange-500/15 dark:text-orange-400",
}

export function formatStatusLabel(status: string): string {
  return status
    .split("_")
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(" ")
}

/** Integration connection status, worded the way spec §24 asks for
 * ("Not Configured" / "Connection Error" / "Connected") rather than the
 * raw enum label — the backend only ever sets `disconnected` for the
 * "no credentials" case (see `IntegrationService.run_health_check`), so
 * that mapping is safe and exhaustive.
 */
export function formatIntegrationStatusLabel(status: string): string {
  switch (status) {
    case "connected":
      return "Connected"
    case "disconnected":
      return "Not Configured"
    case "error":
      return "Connection Error"
    case "syncing":
      return "Syncing"
    case "disabled":
      return "Disabled"
    default:
      return formatStatusLabel(status)
  }
}

const ORDER_STATUS_TONES: Record<string, StatusTone> = {
  pending: "neutral",
  confirmed: "info",
  processing: "info",
  packed: "info",
  shipped: "info",
  delivered: "success",
  cancelled: "danger",
}

const PAYMENT_STATUS_TONES: Record<string, StatusTone> = {
  pending: "neutral",
  authorized: "info",
  paid: "success",
  unpaid: "danger",
  failed: "danger",
  refunded: "warning",
  partially_refunded: "warning",
  // payment_type values share this same tone map (both breakdowns are
  // rendered with domain="payment") — cod/prepaid keys are disjoint from
  // the payment_status keys above, so this is a safe additive mapping.
  cod: "warning",
  prepaid: "info",
}

const SHIPMENT_STATUS_TONES: Record<string, StatusTone> = {
  pending: "neutral",
  picked_up: "info",
  in_transit: "info",
  out_for_delivery: "purple",
  delivered: "success",
  ndr: "orange",
  rto_initiated: "warning",
  rto_delivered: "danger",
  cancelled: "danger",
}

const NDR_STATUS_TONES: Record<string, StatusTone> = {
  open: "orange",
  customer_contacted: "info",
  reattempt_scheduled: "info",
  resolved: "success",
  rto_initiated: "danger",
}

const RTO_STATUS_TONES: Record<string, StatusTone> = {
  initiated: "warning",
  in_transit: "info",
  received: "success",
  cancelled: "danger",
}

const RETURN_STATUS_TONES: Record<string, StatusTone> = {
  requested: "neutral",
  approved: "info",
  rejected: "danger",
  in_transit: "info",
  received: "info",
  completed: "success",
  cancelled: "danger",
}

const REFUND_STATUS_TONES: Record<string, StatusTone> = {
  pending: "neutral",
  processing: "info",
  completed: "success",
  failed: "danger",
  cancelled: "danger",
}

const PRODUCT_STATUS_TONES: Record<string, StatusTone> = {
  active: "success",
  draft: "neutral",
  archived: "danger",
}

const FULFILLMENT_STATUS_TONES: Record<string, StatusTone> = {
  unfulfilled: "warning",
  partial: "warning",
  fulfilled: "success",
}

// Shopify `Fulfillment.displayStatus` values (the actual delivery/
// shipment-progress status — see `Order.shopify_shipment_status`) — a
// distinct vocabulary from `SHIPMENT_STATUS_TONES` above, which is
// Shiprocket's own `ShipmentStatus` enum. Kept as a separate domain/map
// so the two sources are never visually or semantically conflated.
const SHOPIFY_SHIPMENT_STATUS_TONES: Record<string, StatusTone> = {
  submitted: "neutral",
  confirmed: "info",
  label_purchased: "neutral",
  label_printed: "neutral",
  label_voided: "danger",
  carrier_picked_up: "info",
  picked_up: "info",
  ready_for_pickup: "neutral",
  in_transit: "info",
  out_for_delivery: "purple",
  delayed: "warning",
  attempted_delivery: "warning",
  delivered: "success",
  not_delivered: "danger",
  failure: "danger",
  canceled: "danger",
  fulfilled: "success",
  marked_as_fulfilled: "success",
}

const SHIPMENT_DELAY_TONES: Record<string, StatusTone> = {
  on_time: "success",
  at_risk: "warning",
  delayed: "warning",
  unknown: "neutral",
}

const INTEGRATION_STATUS_TONES: Record<string, StatusTone> = {
  connected: "success",
  disconnected: "neutral",
  error: "danger",
  syncing: "info",
  disabled: "neutral",
}

const SYNC_JOB_STATUS_TONES: Record<string, StatusTone> = {
  queued: "neutral",
  running: "info",
  completed: "success",
  partial: "warning",
  failed: "danger",
  cancelled: "neutral",
}

const WEBHOOK_EVENT_STATUS_TONES: Record<string, StatusTone> = {
  received: "neutral",
  processing: "info",
  processed: "success",
  failed: "danger",
  ignored: "neutral",
}

const RECONCILIATION_RUN_STATUS_TONES: Record<string, StatusTone> = {
  running: "info",
  completed: "success",
  failed: "danger",
}

const TELECALLING_STATUS_TONES: Record<string, StatusTone> = {
  not_called: "neutral",
  call_attempted: "info",
  connected: "info",
  not_received: "warning",
  busy: "warning",
  switched_off: "warning",
  invalid_number: "warning",
  call_back_requested: "warning",
  interested: "info",
  not_interested: "danger",
  follow_up_required: "warning",
  confirmed: "success",
  cancelled: "danger",
}

// `app.models.enums.LeadPriority` — computed, never stored (see
// `app.services.lead_classification`).
const LEAD_PRIORITY_TONES: Record<string, StatusTone> = {
  high: "danger",
  medium: "warning",
  low: "neutral",
}

// `app.models.enums.LeadCategory`.
const LEAD_CATEGORY_TONES: Record<string, StatusTone> = {
  abandoned_checkout: "purple",
  cod_unfulfilled: "warning",
  cod_fulfilled: "info",
  prepaid: "success",
}

const RECONCILIATION_RESULT_STATUS_TONES: Record<string, StatusTone> = {
  reconciled: "success",
  mismatch: "warning",
  missing: "danger",
  error: "danger",
}

// Cashfree `payment_method` values (`data.payment.payment_method`'s one
// populated key — see `app.integrations.cashfree.normalizer`) — an
// unmapped/unrecognized method (a new instrument Cashfree adds later)
// falls back to "neutral" via `getStatusTone`, never breaks.
const PAYMENT_METHOD_TONES: Record<string, StatusTone> = {
  upi: "success",
  card: "info",
  netbanking: "purple",
  app: "orange",
  paylater: "warning",
  emi: "warning",
  cardless_emi: "warning",
}

export type StatusDomain =
  | "order"
  | "payment"
  | "fulfillment"
  | "shipment"
  | "shopify_shipment"
  | "shipment_delay"
  | "ndr"
  | "rto"
  | "return"
  | "refund"
  | "product"
  | "integration"
  | "sync_job"
  | "webhook_event"
  | "reconciliation_run"
  | "reconciliation_result"
  | "telecalling"
  | "payment_method"
  | "lead_priority"
  | "lead_category"

const TONE_MAPS: Record<StatusDomain, Record<string, StatusTone>> = {
  order: ORDER_STATUS_TONES,
  payment: PAYMENT_STATUS_TONES,
  fulfillment: FULFILLMENT_STATUS_TONES,
  shipment: SHIPMENT_STATUS_TONES,
  shopify_shipment: SHOPIFY_SHIPMENT_STATUS_TONES,
  shipment_delay: SHIPMENT_DELAY_TONES,
  ndr: NDR_STATUS_TONES,
  rto: RTO_STATUS_TONES,
  return: RETURN_STATUS_TONES,
  refund: REFUND_STATUS_TONES,
  product: PRODUCT_STATUS_TONES,
  integration: INTEGRATION_STATUS_TONES,
  sync_job: SYNC_JOB_STATUS_TONES,
  webhook_event: WEBHOOK_EVENT_STATUS_TONES,
  reconciliation_run: RECONCILIATION_RUN_STATUS_TONES,
  reconciliation_result: RECONCILIATION_RESULT_STATUS_TONES,
  telecalling: TELECALLING_STATUS_TONES,
  payment_method: PAYMENT_METHOD_TONES,
  lead_priority: LEAD_PRIORITY_TONES,
  lead_category: LEAD_CATEGORY_TONES,
}

export function getStatusTone(domain: StatusDomain, status: string): StatusTone {
  return TONE_MAPS[domain][status] ?? "neutral"
}

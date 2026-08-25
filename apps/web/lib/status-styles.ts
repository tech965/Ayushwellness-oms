export type StatusTone = "neutral" | "info" | "success" | "warning" | "danger"

export const STATUS_TONE_CLASSES: Record<StatusTone, string> = {
  neutral: "bg-muted text-muted-foreground",
  info: "bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-400",
  success: "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/30 dark:text-emerald-400",
  warning: "bg-amber-100 text-amber-800 dark:bg-amber-900/30 dark:text-amber-400",
  danger: "bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-400",
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
  failed: "danger",
  refunded: "warning",
  partially_refunded: "warning",
}

const SHIPMENT_STATUS_TONES: Record<string, StatusTone> = {
  pending: "neutral",
  picked_up: "info",
  in_transit: "info",
  out_for_delivery: "info",
  delivered: "success",
  ndr: "warning",
  rto_initiated: "warning",
  rto_delivered: "danger",
  cancelled: "danger",
}

const NDR_STATUS_TONES: Record<string, StatusTone> = {
  open: "warning",
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

const SHIPMENT_DELAY_TONES: Record<string, StatusTone> = {
  on_time: "success",
  at_risk: "warning",
  delayed: "danger",
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

const RECONCILIATION_RESULT_STATUS_TONES: Record<string, StatusTone> = {
  reconciled: "success",
  mismatch: "warning",
  missing: "danger",
  error: "danger",
}

export type StatusDomain =
  | "order"
  | "payment"
  | "shipment"
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

const TONE_MAPS: Record<StatusDomain, Record<string, StatusTone>> = {
  order: ORDER_STATUS_TONES,
  payment: PAYMENT_STATUS_TONES,
  shipment: SHIPMENT_STATUS_TONES,
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
}

export function getStatusTone(domain: StatusDomain, status: string): StatusTone {
  return TONE_MAPS[domain][status] ?? "neutral"
}

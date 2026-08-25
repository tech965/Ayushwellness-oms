// Mirrors app/schemas/integration.py

export type IntegrationStatus =
  "connected" | "disconnected" | "error" | "syncing" | "disabled"
export type IntegrationTypeValue = "ecommerce" | "courier" | "messaging" | "social"

export interface Integration {
  id: string
  name: string
  code: string
  type: IntegrationTypeValue
  status: IntegrationStatus
  enabled: boolean
  configuration: Record<string, unknown> | null
  last_sync_at: string | null
  last_successful_sync_at: string | null
  last_failure_at: string | null
  last_failure_message: string | null
  created_at: string
  updated_at: string
}

export interface IntegrationHealth {
  connected: boolean
  status: IntegrationStatus
  response_time_ms: number | null
  last_successful_sync_at: string | null
  last_failure_at: string | null
  error_message: string | null
}

export type SyncType = "full" | "incremental" | "webhook"
export type SyncJobStatus =
  "queued" | "running" | "completed" | "partial" | "failed" | "cancelled"

export interface SyncJob {
  id: string
  integration_id: string
  sync_type: SyncType
  entity_type: string
  status: SyncJobStatus
  started_at: string | null
  completed_at: string | null
  records_received: number
  records_created: number
  records_updated: number
  records_skipped: number
  records_failed: number
  error_count: number
  job_metadata: Record<string, unknown> | null
  created_at: string
  updated_at: string
}

export type WebhookEventStatus =
  "received" | "processing" | "processed" | "failed" | "ignored"

export interface WebhookEvent {
  id: string
  integration_id: string
  event_type: string
  external_event_id: string
  external_resource_id: string | null
  received_at: string
  processed_at: string | null
  status: WebhookEventStatus
  retry_count: number
  error_message: string | null
  created_at: string
}

export const SYNC_JOB_STATUS_OPTIONS: { label: string; value: SyncJobStatus }[] = [
  { label: "Queued", value: "queued" },
  { label: "Running", value: "running" },
  { label: "Completed", value: "completed" },
  { label: "Partial", value: "partial" },
  { label: "Failed", value: "failed" },
  { label: "Cancelled", value: "cancelled" },
]

export interface WhatsAppStatus {
  configured: boolean
  missing: string[]
}

export interface Contact {
  id: string
  name: string
  phone_number: string
  opted_in: boolean
  created_at: string
}

export interface Group {
  id: string
  name: string
  member_count: number
  created_at: string
}

export type ScheduleKind = "one_time" | "recurring_daily" | "daily_sales_report"
export type MessageType = "text" | "template" | "image"

export interface RecipientSpec {
  contact_id?: string | null
  group_id?: string | null
}

export interface Schedule {
  id: string
  name: string
  schedule_kind: ScheduleKind
  message_type: MessageType
  template_name: string | null
  template_language: string | null
  body_text: string | null
  media_id: string | null
  media_link: string | null
  send_at: string | null
  time_of_day: string | null
  timezone: string
  is_enabled: boolean
  is_paused: boolean
  last_queued_for_date: string | null
  recipient_count: number
  created_at: string
  updated_at: string
}

export interface ScheduleCreateInput {
  name: string
  schedule_kind: ScheduleKind
  message_type: MessageType
  template_name?: string
  template_language?: string
  body_text?: string
  media_id?: string
  media_link?: string
  send_at?: string
  time_of_day?: string
  timezone?: string
  recipients: RecipientSpec[]
}

export type QueueStatus =
  | "QUEUED"
  | "SENDING"
  | "SENT"
  | "DELIVERED"
  | "READ"
  | "FAILED"
  | "CANCELLED"

export interface QueuedMessage {
  id: string
  schedule_id: string | null
  schedule_name: string | null
  contact_id: string
  contact_name: string
  contact_phone: string
  message_type: MessageType
  scheduled_send_at: string
  status: QueueStatus
  provider_message_id: string | null
  failure_reason: string | null
  retry_count: number
  sent_at: string | null
  delivered_at: string | null
  read_at: string | null
  created_at: string
}

export interface DashboardSummary {
  active_schedules: number
  paused_schedules: number
  pending_messages: number
  sent_today: number
  delivered_today: number
  failed_today: number
  next_report_run_utc: string
  latest_daily_report_status: string
}

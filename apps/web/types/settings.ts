export interface GeneralSettings {
  organization_name: string
  oms_display_name: string
  default_timezone: "Asia/Kolkata"
  currency: string
  date_format: "DD MMM YYYY" | "MM/DD/YYYY" | "DD/MM/YYYY" | "YYYY-MM-DD"
  default_page_size: number
}

export interface OrderSettings {
  default_order_status:
    | "pending"
    | "confirmed"
    | "processing"
    | "packed"
    | "shipped"
    | "delivered"
  auto_refresh_interval_seconds: number
  default_sort_field: "order_datetime" | "total_amount" | "order_number"
  default_sort_direction: "asc" | "desc"
}

export interface NotificationSettings {
  email_order_notifications: boolean
  email_shipment_notifications: boolean
  email_return_refund_notifications: boolean
}

export interface ShippingSettings {
  default_courier_id: string | null
  tracking_refresh_interval_minutes: number
}

export type DashboardDateRangeSetting =
  | "today"
  | "yesterday"
  | "this_week"
  | "last_7_days"
  | "last_30_days"
  | "this_month"

export interface DashboardSettings {
  default_date_range: DashboardDateRangeSetting
  default_chart_interval: "day" | "week" | "month"
  refresh_interval_seconds: number
}

export interface SecuritySettings {
  session_timeout_minutes: number
}

export interface AppearanceSettings {
  table_density: "compact" | "comfortable"
}

export interface AppSettingsData {
  general: GeneralSettings
  orders: OrderSettings
  notifications: NotificationSettings
  shipping: ShippingSettings
  dashboard: DashboardSettings
  security: SecuritySettings
  appearance: AppearanceSettings
}

export interface AppSettingsResponse {
  settings: AppSettingsData
  updated_at: string | null
  updated_by_email: string | null
}

export type AppSettingsUpdateRequest = Partial<AppSettingsData>

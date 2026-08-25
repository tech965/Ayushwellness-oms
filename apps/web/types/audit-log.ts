// Mirrors app/schemas/audit_log.py

export interface AuditLog {
  id: string
  user_id: string | null
  action: string
  entity_type: string
  entity_id: string
  previous_value: Record<string, unknown> | null
  new_value: Record<string, unknown> | null
  ip_address: string | null
  user_agent: string | null
  audit_metadata: Record<string, unknown> | null
  created_at: string
}

export interface AuditLogListFilters {
  entity_type?: string
  entity_id?: string
  user_id?: string
  action?: string
  date_from?: string
  date_to?: string
}

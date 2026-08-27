/** Mirrors apps/api/app/schemas/telecalling.py. */

export type TelecallingStatus =
  | "not_called"
  | "call_attempted"
  | "connected"
  | "not_received"
  | "busy"
  | "switched_off"
  | "invalid_number"
  | "call_back_requested"
  | "interested"
  | "not_interested"
  | "follow_up_required"
  | "confirmed"
  | "cancelled"

export const CALL_OUTCOME_OPTIONS: { label: string; value: TelecallingStatus }[] = [
  { label: "Call Attempted", value: "call_attempted" },
  { label: "Connected", value: "connected" },
  { label: "Not Received", value: "not_received" },
  { label: "Busy", value: "busy" },
  { label: "Switched Off", value: "switched_off" },
  { label: "Invalid Number", value: "invalid_number" },
  { label: "Call Back Requested", value: "call_back_requested" },
  { label: "Interested", value: "interested" },
  { label: "Not Interested", value: "not_interested" },
  { label: "Follow-up Required", value: "follow_up_required" },
  { label: "Confirmed", value: "confirmed" },
  { label: "Cancelled", value: "cancelled" },
]

export interface AssignedOrder {
  order_id: string
  order_number: string
  customer_name: string | null
  customer_phone: string | null
  item_summary: string | null
  total_amount: string
  payment_type: string
  payment_status: string
  fulfillment_status: string
  order_datetime: string
  shipping_address: {
    line1?: string
    line2?: string
    city?: string
    state?: string
    pin_code?: string
    country?: string
    contact_name?: string
    contact_phone?: string
  } | null
  assignment_id: string | null
  assigned_to: string | null
  assigned_to_name: string | null
  call_status: TelecallingStatus | null
  attempt_count: number
  last_attempt_at: string | null
  next_follow_up_at: string | null
}

export interface CallAttempt {
  id: string
  order_id: string
  telecaller_id: string | null
  attempt_number: number
  attempted_at: string
  outcome: TelecallingStatus
  notes: string | null
  next_follow_up_at: string | null
  created_at: string
}

export interface CallHistoryEntry extends CallAttempt {
  order_number: string
}

export interface OrderAssignment {
  id: string
  order_id: string
  assigned_to: string
  assigned_by: string | null
  assigned_at: string
  team_leader_id: string | null
  assignment_status: "active" | "inactive"
  reassigned_from: string | null
  reassigned_to: string | null
  reassigned_at: string | null
  reassignment_reason: string | null
  current_status: TelecallingStatus
  attempt_count: number
  last_attempt_at: string | null
  next_follow_up_at: string | null
}

export interface TelecallerPerformance {
  telecaller_id: string
  telecaller_name: string
  assigned: number
  called: number
  connected: number
  follow_ups: number
  confirmed: number
  not_interested: number
}

export interface TelecallingSummary {
  assigned: number
  pending: number
  called: number
  connected: number
  follow_ups_today: number
  confirmed: number
  not_interested: number
}

export interface AssignOrdersInput {
  order_ids: string[]
  mode: "manual" | "equal"
  telecaller_id?: string
  telecaller_ids?: string[]
}

export interface ReassignOrderInput {
  order_id: string
  new_telecaller_id: string
  reason: string
}

export interface LogCallInput {
  outcome: TelecallingStatus
  notes?: string
  next_follow_up_at?: string
}

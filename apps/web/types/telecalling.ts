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

/** Mirrors app.models.enums.LeadCategory. */
export type LeadCategory =
  "abandoned_checkout" | "cod_unfulfilled" | "cod_fulfilled" | "prepaid"

/** Mirrors app.models.enums.LeadPriority. */
export type LeadPriority = "high" | "medium" | "low"

export const LEAD_CATEGORY_OPTIONS: { label: string; value: LeadCategory }[] = [
  { label: "COD Unfulfilled", value: "cod_unfulfilled" },
  { label: "COD Fulfilled", value: "cod_fulfilled" },
  { label: "Prepaid", value: "prepaid" },
]

export const LEAD_CATEGORY_LABELS: Record<LeadCategory, string> = {
  abandoned_checkout: "Abandoned Checkout",
  cod_unfulfilled: "COD Unfulfilled",
  cod_fulfilled: "COD Fulfilled",
  prepaid: "Prepaid",
}

export const LEAD_PRIORITY_LABELS: Record<LeadPriority, string> = {
  high: "High",
  medium: "Medium",
  low: "Low",
}

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
  lead_category: LeadCategory | null
  priority: LeadPriority | null
}

export interface AssignedCheckout {
  checkout_id: string
  customer_name: string | null
  customer_phone: string | null
  customer_email: string | null
  item_summary: string | null
  total_amount: string
  checkout_url: string | null
  checkout_created_at: string | null
  is_recovered: boolean
  assignment_id: string | null
  assigned_to: string | null
  assigned_to_name: string | null
  call_status: TelecallingStatus | null
  attempt_count: number
  last_attempt_at: string | null
  next_follow_up_at: string | null
  lead_category: "abandoned_checkout"
  priority: LeadPriority | null
}

export interface CheckoutAssignment {
  id: string
  checkout_id: string
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

export interface CheckoutCallAttempt {
  id: string
  checkout_id: string
  telecaller_id: string | null
  attempt_number: number
  attempted_at: string
  outcome: TelecallingStatus
  notes: string | null
  next_follow_up_at: string | null
  created_at: string
}

export interface AssignCheckoutsInput {
  checkout_ids: string[]
  mode: "manual" | "equal"
  telecaller_id?: string
  telecaller_ids?: string[]
}

export interface ReassignCheckoutInput {
  checkout_id: string
  new_telecaller_id: string
  reason: string
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

/** One selectable entry for a "Select Telecaller" assignment dropdown —
 * every active TELECALLER-role user in scope, regardless of whether
 * they've ever had a lead assigned yet. Distinct from
 * `TelecallerPerformance` below (assignment-activity counts only, so a
 * brand-new Telecaller never appears there).
 */
export interface TelecallerOption {
  id: string
  name: string
  email: string
}

export interface TelecallerPerformance {
  telecaller_id: string
  telecaller_name: string
  assigned: number
  called: number
  pending: number
  connected: number
  interested: number
  follow_ups: number
  confirmed: number
  not_interested: number
  conversion_rate: number
}

export interface TelecallingSummary {
  total_leads: number
  unassigned_leads: number
  assigned: number
  pending: number
  called: number
  connected: number
  follow_ups_today: number
  confirmed: number
  not_interested: number
  abandoned_checkouts: number
  cod_unfulfilled: number
  cod_fulfilled: number
  prepaid: number
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

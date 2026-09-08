// Mirrors app/schemas/user.py

export interface User {
  id: string
  name: string
  email: string
  phone: string | null
  is_active: boolean
  is_superuser: boolean
  team_leader_id: string | null
  // Set only for Telecaller accounts -- which Shipment Staff user
  // processes shipments for orders this Telecaller confirms. See
  // app.models.auth.User.shipment_staff_id.
  shipment_staff_id: string | null
  created_at: string
  updated_at: string
  /** Role names (e.g. "TEAM_LEADER"), not ids -- `UserResponse` is
   * serialized server-side from `user.role_names`. */
  roles: string[]
}

// Mirrors app/schemas/user.py:UserCreateRequest
export interface UserCreateInput {
  name: string
  email: string
  phone?: string | null
  password: string
  role_ids: string[]
  shipment_staff_id?: string | null
}

// Mirrors app/schemas/user.py:UserUpdateRequest (`team_leader_id`/
// `clear_team_leader` belong to the Telecalling feature, still out of
// scope for this UI; `shipment_staff_id`/`clear_shipment_staff` are the
// one addition this page's Edit dialog actually uses).
export interface UserUpdateInput {
  name?: string
  phone?: string | null
  is_active?: boolean
  role_ids?: string[]
  shipment_staff_id?: string | null
  clear_shipment_staff?: boolean
}

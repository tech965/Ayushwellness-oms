// Mirrors app/schemas/user.py

export interface User {
  id: string
  name: string
  email: string
  phone: string | null
  is_active: boolean
  is_superuser: boolean
  team_leader_id: string | null
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
}

// Mirrors app/schemas/user.py:UserUpdateRequest (only the fields this UI
// ever sends -- `team_leader_id`/`clear_team_leader` belong to the
// Telecalling feature, out of scope here).
export interface UserUpdateInput {
  name?: string
  phone?: string | null
  is_active?: boolean
  role_ids?: string[]
}

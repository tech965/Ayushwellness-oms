// Mirrors app/schemas/rbac.py

export interface Role {
  id: string
  name: string
  description: string | null
  created_at: string
  updated_at: string
  /** Permission codes (e.g. "users.manage"), not ids -- `RoleResponse`
   * serializes `role.role_permissions` down to `[rp.permission.code ...]`
   * server-side. */
  permissions: string[]
}

// Mirrors app/schemas/rbac.py:RoleCreateRequest
export interface RoleCreateInput {
  name: string
  description?: string | null
  permission_ids: string[]
}

// Mirrors app/schemas/rbac.py:RoleUpdateRequest -- deliberately has no
// `name` field: role name is immutable after creation (PATCH /roles/{id}
// only ever accepts description/permission_ids server-side).
export interface RoleUpdateInput {
  description?: string | null
  permission_ids?: string[]
}

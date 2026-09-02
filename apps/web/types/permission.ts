// Mirrors app/schemas/rbac.py:PermissionResponse

export interface Permission {
  id: string
  code: string
  module: string
  action: string
  description: string | null
}

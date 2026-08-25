// Mirrors app/schemas/auth.py:AccessTokenResponse
export interface AccessTokenResult {
  access_token: string
  token_type: string
}

// Mirrors app/schemas/auth.py:CurrentUserResponse
export interface CurrentUser {
  id: string
  name: string
  email: string
  phone: string | null
  is_active: boolean
  is_superuser: boolean
  roles: string[]
  permissions: string[]
}

import {
  apiClient,
  getStoredRefreshToken,
  setStoredAccessToken,
  setStoredRefreshToken,
} from "@/lib/api-client"
import type { ApiResponse } from "@/types/api"
import type { CurrentUser } from "@/types/auth"
import type { LoginInput } from "@/lib/validation/auth"

export interface LoginResult {
  access_token: string
  refresh_token: string
  token_type: string
}

export async function login(input: LoginInput): Promise<LoginResult> {
  const response = await apiClient.post<ApiResponse<LoginResult>>("/auth/login", input)
  const result = response.data.data
  if (!result) {
    throw new Error("Login response did not include a token.")
  }
  setStoredAccessToken(result.access_token)
  setStoredRefreshToken(result.refresh_token)
  return result
}

export async function logout(): Promise<void> {
  const refreshToken = getStoredRefreshToken()
  try {
    if (refreshToken) {
      await apiClient.post("/auth/logout", { refresh_token: refreshToken })
    }
  } finally {
    setStoredAccessToken(null)
    setStoredRefreshToken(null)
  }
}

export async function getCurrentUser(): Promise<CurrentUser> {
  const response = await apiClient.get<ApiResponse<CurrentUser>>("/auth/me")
  const data = response.data.data
  if (!data) {
    throw new Error("Current user response did not include data.")
  }
  return data
}

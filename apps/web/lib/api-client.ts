import axios, { AxiosError, type InternalAxiosRequestConfig } from "axios"

import type { AccessTokenResult } from "@/types/auth"
import type { ApiErrorResponse, ApiResponse } from "@/types/api"

const ACCESS_TOKEN_STORAGE_KEY = "oms_access_token"
const REFRESH_TOKEN_STORAGE_KEY = "oms_refresh_token"

/**
 * Central axios instance for all backend calls. Auth token attachment,
 * 401-triggered refresh, and error normalization are wired here so every
 * feature service (orders, customers, ...) gets it for free.
 */
export const apiClient = axios.create({
  baseURL: process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000/api/v1",
  timeout: 15_000,
})

export function getStoredAccessToken(): string | null {
  if (typeof window === "undefined") return null
  return window.localStorage.getItem(ACCESS_TOKEN_STORAGE_KEY)
}

export function setStoredAccessToken(token: string | null): void {
  if (typeof window === "undefined") return
  if (token) {
    window.localStorage.setItem(ACCESS_TOKEN_STORAGE_KEY, token)
  } else {
    window.localStorage.removeItem(ACCESS_TOKEN_STORAGE_KEY)
  }
}

export function getStoredRefreshToken(): string | null {
  if (typeof window === "undefined") return null
  return window.localStorage.getItem(REFRESH_TOKEN_STORAGE_KEY)
}

export function setStoredRefreshToken(token: string | null): void {
  if (typeof window === "undefined") return
  if (token) {
    window.localStorage.setItem(REFRESH_TOKEN_STORAGE_KEY, token)
  } else {
    window.localStorage.removeItem(REFRESH_TOKEN_STORAGE_KEY)
  }
}

function clearStoredTokens(): void {
  setStoredAccessToken(null)
  setStoredRefreshToken(null)
}

apiClient.interceptors.request.use((config: InternalAxiosRequestConfig) => {
  const token = getStoredAccessToken()
  if (token) {
    config.headers.set("Authorization", `Bearer ${token}`)
  }
  return config
})

type RetriableRequestConfig = InternalAxiosRequestConfig & { _retried?: boolean }

// Coalesces concurrent 401s into a single /auth/refresh call instead of
// firing one refresh request per failed request.
let refreshInFlight: Promise<string | null> | null = null

async function refreshAccessToken(): Promise<string | null> {
  const refreshToken = getStoredRefreshToken()
  if (!refreshToken) return null

  try {
    const response = await apiClient.post<ApiResponse<AccessTokenResult>>(
      "/auth/refresh",
      {
        refresh_token: refreshToken,
      }
    )
    const accessToken = response.data.data?.access_token ?? null
    if (accessToken) setStoredAccessToken(accessToken)
    return accessToken
  } catch {
    return null
  }
}

apiClient.interceptors.response.use(
  (response) => response,
  async (error: AxiosError<ApiErrorResponse>) => {
    const originalRequest = error.config as RetriableRequestConfig | undefined
    const isAuthEndpoint = originalRequest?.url?.includes("/auth/") ?? false

    if (
      error.response?.status === 401 &&
      originalRequest &&
      !originalRequest._retried &&
      !isAuthEndpoint
    ) {
      originalRequest._retried = true
      refreshInFlight ??= refreshAccessToken().finally(() => {
        refreshInFlight = null
      })
      const newAccessToken = await refreshInFlight

      if (newAccessToken) {
        originalRequest.headers.set("Authorization", `Bearer ${newAccessToken}`)
        return apiClient(originalRequest)
      }

      clearStoredTokens()
      if (typeof window !== "undefined") {
        // A full navigation, not a router push: this runs inside an axios
        // interceptor, outside any React component/render tree, so
        // useRouter() isn't available here — and a hard reload is the
        // right call anyway, to guarantee every in-memory query/auth
        // state is dropped along with the now-invalid session.
        // eslint-disable-next-line @next/next/no-location-assign-relative-destination
        window.location.assign("/login")
      }
    }

    return Promise.reject(error)
  }
)

export function getApiErrorMessage(error: unknown): string {
  if (axios.isAxiosError<ApiErrorResponse>(error)) {
    return error.response?.data?.error?.message ?? error.message
  }
  if (error instanceof Error) return error.message
  return "An unexpected error occurred."
}

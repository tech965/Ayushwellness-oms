import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import type { AxiosAdapter, AxiosResponse } from "axios"

import { AxiosError } from "axios"

import {
  apiClient,
  getApiErrorMessage,
  getStoredAccessToken,
  setStoredAccessToken,
  setStoredRefreshToken,
} from "@/lib/api-client"

/**
 * Regression test for the auth-refresh-loop bug: `/auth/me` (and any other
 * `/auth/*` endpoint besides login/refresh) must go through the normal
 * 401 -> refresh -> retry flow like any authenticated request. The old
 * `url?.includes("/auth/")` check excluded `/auth/me` from ever retrying,
 * so an expired access token with a perfectly valid refresh token still
 * bounced the user to /login (see `apps/web/lib/api-client.ts`).
 */
describe("apiClient 401 refresh-and-retry", () => {
  const originalAdapter = apiClient.defaults.adapter

  beforeEach(() => {
    setStoredAccessToken("expired-access-token")
    setStoredRefreshToken("valid-refresh-token")
  })

  afterEach(() => {
    apiClient.defaults.adapter = originalAdapter
    setStoredAccessToken(null)
    setStoredRefreshToken(null)
    vi.restoreAllMocks()
  })

  function jsonResponse(data: unknown, status: number): AxiosResponse {
    return {
      data,
      status,
      statusText: String(status),
      headers: {},
      config: {} as never,
    }
  }

  it("retries a 401 from /auth/me after a successful token refresh, instead of giving up immediately", async () => {
    let authMeCalls = 0
    let refreshCalls = 0

    const adapter: AxiosAdapter = async (config) => {
      const url = config.url ?? ""
      if (url.endsWith("/auth/me")) {
        authMeCalls += 1
        const token =
          config.headers?.get?.("Authorization") ?? config.headers?.Authorization
        if (token === "Bearer new-access-token") {
          return jsonResponse(
            { success: true, data: { id: "1", permissions: [], is_superuser: false } },
            200
          )
        }
        const error = new Error("Unauthorized") as Error & {
          config: unknown
          response: unknown
          isAxiosError: boolean
        }
        error.config = config
        error.response = jsonResponse(
          { success: false, error: { code: "unauthorized", message: "Unauthorized" } },
          401
        )
        error.isAxiosError = true
        throw error
      }
      if (url.endsWith("/auth/refresh")) {
        refreshCalls += 1
        return jsonResponse(
          {
            success: true,
            data: {
              access_token: "new-access-token",
              refresh_token: "new-refresh-token",
            },
          },
          200
        )
      }
      throw new Error(`Unexpected request to ${url}`)
    }
    apiClient.defaults.adapter = adapter

    const response = await apiClient.get("/auth/me")

    expect(response.status).toBe(200)
    expect(authMeCalls).toBe(2) // original (401) + retried (200) — never fewer, never looping
    expect(refreshCalls).toBe(1)
    expect(getStoredAccessToken()).toBe("new-access-token")
  })

  it("clears tokens and stops (does not loop) when the refresh token is also invalid", async () => {
    const assign = vi.fn()
    Object.defineProperty(window, "location", {
      value: { ...window.location, assign },
      writable: true,
    })
    let authMeCalls = 0
    let refreshCalls = 0

    const adapter: AxiosAdapter = async (config) => {
      const url = config.url ?? ""
      if (url.endsWith("/auth/me")) {
        authMeCalls += 1
        const error = new Error("Unauthorized") as Error & {
          config: unknown
          response: unknown
          isAxiosError: boolean
        }
        error.config = config
        error.response = jsonResponse(
          { success: false, error: { code: "unauthorized", message: "Unauthorized" } },
          401
        )
        error.isAxiosError = true
        throw error
      }
      if (url.endsWith("/auth/refresh")) {
        refreshCalls += 1
        const error = new Error("Unauthorized") as Error & {
          config: unknown
          response: unknown
          isAxiosError: boolean
        }
        error.config = config
        error.response = jsonResponse(
          { success: false, error: { code: "unauthorized", message: "Unauthorized" } },
          401
        )
        error.isAxiosError = true
        throw error
      }
      throw new Error(`Unexpected request to ${url}`)
    }
    apiClient.defaults.adapter = adapter

    await expect(apiClient.get("/auth/me")).rejects.toBeTruthy()

    // Exactly one attempt each — not the unbounded hammering the old bug caused.
    expect(authMeCalls).toBe(1)
    expect(refreshCalls).toBe(1)
    expect(getStoredAccessToken()).toBeNull()
  })
})

/**
 * Regression test for the "Network Error" production incident: a real
 * HTTP 500 with a real JSON error body was showing up in the browser as
 * a generic "Network Error" instead of the server's actual message. The
 * root cause was backend-side (the 500 response was missing CORS
 * headers, so the browser blocked the frontend from ever reading the
 * body — fixed in `apps/api/app/middleware/error_handler.py`'s
 * `UnhandledExceptionMiddleware`). This locks in the frontend half:
 * `getApiErrorMessage` must surface the server's real message whenever
 * a response body is actually present, and only fall back to axios's
 * generic message ("Network Error") for a genuine network-level
 * failure with no response at all.
 */
describe("getApiErrorMessage", () => {
  it("surfaces the server's real error message for a 500 with a JSON body", () => {
    const error = new AxiosError("Request failed with status code 500")
    error.response = {
      data: {
        success: false,
        error: {
          code: "internal_error",
          message: "An unexpected error occurred. Please try again later.",
        },
      },
      status: 500,
      statusText: "Internal Server Error",
      headers: {},
      config: {} as never,
    }

    expect(getApiErrorMessage(error)).toBe(
      "An unexpected error occurred. Please try again later."
    )
    expect(getApiErrorMessage(error)).not.toBe("Network Error")
  })

  it("falls back to the generic axios message only when there is truly no response", () => {
    const error = new AxiosError("Network Error")
    // No `.response` set — this is what a genuine network-level failure
    // (DNS, connection refused, CORS-blocked) looks like to axios.

    expect(getApiErrorMessage(error)).toBe("Network Error")
  })
})

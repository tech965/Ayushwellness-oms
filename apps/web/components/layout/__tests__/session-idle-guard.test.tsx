import { describe, expect, it, vi } from "vitest"
import { render } from "@testing-library/react"

import { SessionIdleGuard } from "@/components/layout/session-idle-guard"
import { useAuth } from "@/lib/auth-context"
import { useAppSettings } from "@/lib/settings-context"

vi.mock("@/lib/auth-context", () => ({
  useAuth: vi.fn(),
}))

vi.mock("@/lib/settings-context", () => ({
  useAppSettings: vi.fn(),
}))

const mockedUseAuth = vi.mocked(useAuth)
const mockedUseAppSettings = vi.mocked(useAppSettings)

describe("SessionIdleGuard resilience", () => {
  it("TEST 8: an authenticated user stays authenticated (never auto-logged-out) when Settings failed to load", () => {
    const logout = vi.fn()
    mockedUseAuth.mockReturnValue({
      user: { id: "1", name: "Test", email: "t@example.com" },
      logout,
    } as unknown as ReturnType<typeof useAuth>)
    // Settings unavailable -- context falls back to `undefined`, exactly
    // as it does on a real Settings API failure.
    mockedUseAppSettings.mockReturnValue(undefined)

    expect(() => render(<SessionIdleGuard />)).not.toThrow()
    // `session_timeout_minutes` defaults to 0 (disabled) when settings are
    // unavailable -- no idle timer is armed, so a missing Settings
    // response can never cause a spurious logout.
    expect(logout).not.toHaveBeenCalled()
  })
})

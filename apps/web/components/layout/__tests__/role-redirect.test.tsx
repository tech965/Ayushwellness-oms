import { describe, expect, it, vi } from "vitest"

import { renderWithProviders } from "@/test-utils/render-with-providers"
import { RoleRedirect } from "@/components/layout/role-redirect"
import { useAuth } from "@/lib/auth-context"

const mockReplace = vi.fn()
const mockUsePathname = vi.fn<() => string>()

vi.mock("next/navigation", () => ({
  usePathname: () => mockUsePathname(),
  useRouter: () => ({ replace: mockReplace, push: vi.fn(), back: vi.fn() }),
}))

vi.mock("@/lib/auth-context", () => ({
  useAuth: vi.fn(),
}))

const mockedUseAuth = vi.mocked(useAuth)

function authWithRoles(roles: string[]) {
  return {
    user: {
      id: "1",
      name: "Test",
      email: "t@example.com",
      phone: null,
      is_active: true,
      is_superuser: false,
      roles,
      permissions: [],
    },
    permissions: new Set<string>(),
    isLoading: false,
    hasPermission: () => true,
    hasRole: (role: string) => roles.includes(role),
    logout: vi.fn(),
  }
}

function authLoading() {
  return {
    user: null,
    permissions: new Set<string>(),
    isLoading: true,
    hasPermission: () => false,
    hasRole: () => false,
    logout: vi.fn(),
  }
}

describe("RoleRedirect", () => {
  it("Team Leader on /team/dashboard stays there (no redirect)", () => {
    mockReplace.mockClear()
    mockUsePathname.mockReturnValue("/team/dashboard")
    mockedUseAuth.mockReturnValue(authWithRoles(["TEAM_LEADER"]))
    renderWithProviders(<RoleRedirect />)
    expect(mockReplace).not.toHaveBeenCalled()
  })

  it("Team Leader on /telecaller/dashboard redirects to /team/dashboard exactly once", () => {
    mockReplace.mockClear()
    mockUsePathname.mockReturnValue("/telecaller/dashboard")
    mockedUseAuth.mockReturnValue(authWithRoles(["TEAM_LEADER"]))
    renderWithProviders(<RoleRedirect />)
    expect(mockReplace).toHaveBeenCalledTimes(1)
    expect(mockReplace).toHaveBeenCalledWith("/team/dashboard")
  })

  it("Telecaller on /telecaller/dashboard stays there (no redirect)", () => {
    mockReplace.mockClear()
    mockUsePathname.mockReturnValue("/telecaller/dashboard")
    mockedUseAuth.mockReturnValue(authWithRoles(["TELECALLER"]))
    renderWithProviders(<RoleRedirect />)
    expect(mockReplace).not.toHaveBeenCalled()
  })

  it("Telecaller on /team/dashboard redirects to /telecaller/dashboard exactly once", () => {
    mockReplace.mockClear()
    mockUsePathname.mockReturnValue("/team/dashboard")
    mockedUseAuth.mockReturnValue(authWithRoles(["TELECALLER"]))
    renderWithProviders(<RoleRedirect />)
    expect(mockReplace).toHaveBeenCalledTimes(1)
    expect(mockReplace).toHaveBeenCalledWith("/telecaller/dashboard")
  })

  it("a user holding BOTH roles never loops -- TEAM_LEADER wins, and settles idempotently", () => {
    // Production incident: this exact combination (roles aren't
    // mutually exclusive under this RBAC model) previously ping-ponged
    // between /team/dashboard and /telecaller/dashboard forever.
    mockReplace.mockClear()
    mockUsePathname.mockReturnValue("/telecaller/dashboard")
    mockedUseAuth.mockReturnValue(authWithRoles(["TELECALLER", "TEAM_LEADER"]))
    const { rerender } = renderWithProviders(<RoleRedirect />)
    expect(mockReplace).toHaveBeenCalledTimes(1)
    expect(mockReplace).toHaveBeenCalledWith("/team/dashboard")

    // Simulate the resulting navigation: pathname is now the resolved
    // target. A second render with the SAME roles must not redirect
    // again -- this is exactly the idempotence check that would have
    // caught the original loop.
    mockReplace.mockClear()
    mockUsePathname.mockReturnValue("/team/dashboard")
    rerender(<RoleRedirect />)
    expect(mockReplace).not.toHaveBeenCalled()
  })

  it("Admin (neither role) is never redirected to either role dashboard", () => {
    mockReplace.mockClear()
    mockUsePathname.mockReturnValue("/dashboard")
    mockedUseAuth.mockReturnValue(authWithRoles(["ADMIN"]))
    renderWithProviders(<RoleRedirect />)
    expect(mockReplace).not.toHaveBeenCalled()
  })

  it("does not redirect while auth is still loading (user is null)", () => {
    mockReplace.mockClear()
    mockUsePathname.mockReturnValue("/dashboard")
    mockedUseAuth.mockReturnValue(authLoading())
    renderWithProviders(<RoleRedirect />)
    expect(mockReplace).not.toHaveBeenCalled()
  })
})

import { describe, expect, it, vi } from "vitest"
import { screen } from "@testing-library/react"

import { renderWithProviders } from "@/test-utils/render-with-providers"
import { SidebarNav } from "@/components/layout/sidebar-nav"
import { useAuth } from "@/lib/auth-context"

vi.mock("next/navigation", () => ({
  usePathname: () => "/dashboard",
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

describe("SidebarNav role-based navigation", () => {
  it("renders the full Admin nav for a user with no special role", () => {
    mockedUseAuth.mockReturnValue(authWithRoles(["ADMIN"]))
    renderWithProviders(<SidebarNav />)
    expect(screen.getByText("Orders")).toBeInTheDocument()
    expect(screen.getByText("Integrations")).toBeInTheDocument()
    expect(screen.getByText("Settings")).toBeInTheDocument()
  })

  it("renders only the minimal Telecaller nav — no admin links", () => {
    mockedUseAuth.mockReturnValue(authWithRoles(["TELECALLER"]))
    renderWithProviders(<SidebarNav />)
    expect(screen.getByText("My Assigned Orders")).toBeInTheDocument()
    expect(screen.getByText("Follow-ups")).toBeInTheDocument()
    expect(screen.getByText("Call History")).toBeInTheDocument()
    expect(screen.queryByText("Orders")).not.toBeInTheDocument()
    expect(screen.queryByText("Users")).not.toBeInTheDocument()
    expect(screen.queryByText("Integrations")).not.toBeInTheDocument()
    expect(screen.queryByText("Reconciliation")).not.toBeInTheDocument()
    expect(screen.queryByText("Settings")).not.toBeInTheDocument()
  })

  it("renders only the minimal Team Leader nav", () => {
    mockedUseAuth.mockReturnValue(authWithRoles(["TEAM_LEADER"]))
    renderWithProviders(<SidebarNav />)
    expect(screen.getByText("Unfulfilled Orders")).toBeInTheDocument()
    expect(screen.getByText("Telecallers")).toBeInTheDocument()
    expect(screen.queryByText("Users")).not.toBeInTheDocument()
    expect(screen.queryByText("Integrations")).not.toBeInTheDocument()
  })
})

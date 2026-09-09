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

function authWithRoles(roles: string[], permissionCodes: string[] = []) {
  const permissions = new Set(permissionCodes)
  return {
    user: {
      id: "1",
      name: "Test",
      email: "t@example.com",
      phone: null,
      is_active: true,
      is_superuser: false,
      roles,
      permissions: permissionCodes,
    },
    permissions,
    isLoading: false,
    hasPermission: (code: string) =>
      permissionCodes.length === 0 ? true : permissions.has(code),
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

describe("SidebarNav loading state", () => {
  it("never renders the full Admin nav (or any role nav) while auth is still loading", () => {
    mockedUseAuth.mockReturnValue(authLoading())
    renderWithProviders(<SidebarNav />)
    // None of the full-nav-only, Fulfillment-only, or Telecaller-only
    // items may appear -- a stale/default fallthrough to `navGroups`
    // here would flash the full Admin navigation before the real role
    // loads, which is exactly the bug this guards against.
    expect(screen.queryByText("Orders")).not.toBeInTheDocument()
    expect(screen.queryByText("Integrations")).not.toBeInTheDocument()
    expect(screen.queryByText("Fulfillment Dashboard")).not.toBeInTheDocument()
    expect(screen.queryByText("My Assigned Orders")).not.toBeInTheDocument()
    expect(screen.getByLabelText("Loading navigation")).toBeInTheDocument()
  })

  it("never renders the full Admin nav while `user` is still null even if isLoading has settled", () => {
    mockedUseAuth.mockReturnValue({ ...authLoading(), isLoading: false, user: null })
    renderWithProviders(<SidebarNav />)
    expect(screen.queryByText("Orders")).not.toBeInTheDocument()
    expect(screen.getByLabelText("Loading navigation")).toBeInTheDocument()
  })
})

describe("SidebarNav role-based navigation", () => {
  it("renders the full Admin nav for a user with no special role", () => {
    mockedUseAuth.mockReturnValue(authWithRoles(["ADMIN"]))
    renderWithProviders(<SidebarNav />)
    expect(screen.getByText("Orders")).toBeInTheDocument()
    expect(screen.getByText("Integrations")).toBeInTheDocument()
    expect(screen.getByText("Settings")).toBeInTheDocument()
  })

  it("includes the Telecalling section (Lead Pool, Abandoned Checkouts, Telecallers) for Admin", () => {
    mockedUseAuth.mockReturnValue(authWithRoles(["ADMIN"]))
    renderWithProviders(<SidebarNav />)
    expect(screen.getByText("Telecalling")).toBeInTheDocument()
    expect(screen.getByText("Lead Pool")).toBeInTheDocument()
    expect(screen.getByText("Abandoned Checkouts")).toBeInTheDocument()
    expect(screen.getByText("Unfulfilled Orders")).toBeInTheDocument()
    expect(screen.getByText("Telecallers")).toBeInTheDocument()
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

  it("renders only the minimal Fulfillment nav — no admin links", () => {
    mockedUseAuth.mockReturnValue(
      authWithRoles(["FULFILLMENT"], ["shipments.read", "ndr.read", "rto.read"])
    )
    renderWithProviders(<SidebarNav />)
    expect(screen.getByText("Fulfillment Dashboard")).toBeInTheDocument()
    expect(screen.getByText("Orders Need Shipment")).toBeInTheDocument()
    expect(screen.getByText("Confirmed by Telecaller")).toBeInTheDocument()
    expect(screen.getByText("Shipments")).toBeInTheDocument()
    expect(screen.queryByText("Orders")).not.toBeInTheDocument()
    expect(screen.queryByText("Users")).not.toBeInTheDocument()
    expect(screen.queryByText("Integrations")).not.toBeInTheDocument()
    expect(screen.queryByText("Telecallers")).not.toBeInTheDocument()
    expect(screen.queryByText("Settings")).not.toBeInTheDocument()
  })

  it("renders only the minimal Shipment Staff nav — no admin links", () => {
    mockedUseAuth.mockReturnValue(authWithRoles(["SHIPMENT_STAFF"], ["shipment_staff.manage"]))
    renderWithProviders(<SidebarNav />)
    expect(screen.getByText("My Shipment Dashboard")).toBeInTheDocument()
    expect(screen.getByText("Orders Need Shipment")).toBeInTheDocument()
    expect(screen.getByText("Shipments")).toBeInTheDocument()
    expect(screen.getByText("NDR")).toBeInTheDocument()
    expect(screen.getByText("RTO")).toBeInTheDocument()
    expect(screen.queryByText("Orders")).not.toBeInTheDocument()
    expect(screen.queryByText("Users")).not.toBeInTheDocument()
    expect(screen.queryByText("Integrations")).not.toBeInTheDocument()
    expect(screen.queryByText("Telecallers")).not.toBeInTheDocument()
    expect(screen.queryByText("Fulfillment Dashboard")).not.toBeInTheDocument()
    expect(screen.queryByText("Settings")).not.toBeInTheDocument()
  })
})

describe("SidebarNav Administration gating", () => {
  it("hides Users and Roles but keeps Settings with neither users.manage nor roles.manage", () => {
    mockedUseAuth.mockReturnValue(authWithRoles(["STAFF"], ["orders.update"]))
    renderWithProviders(<SidebarNav />)
    expect(screen.getByText("Orders")).toBeInTheDocument()
    expect(screen.queryByText("Users")).not.toBeInTheDocument()
    expect(screen.queryByText("Roles")).not.toBeInTheDocument()
    expect(screen.getByText("Settings")).toBeInTheDocument()
  })

  it("shows only Users when the viewer has users.manage but not roles.manage", () => {
    mockedUseAuth.mockReturnValue(authWithRoles(["STAFF"], ["users.manage"]))
    renderWithProviders(<SidebarNav />)
    expect(screen.getByText("Users")).toBeInTheDocument()
    expect(screen.queryByText("Roles")).not.toBeInTheDocument()
    expect(screen.getByText("Settings")).toBeInTheDocument()
  })

  it("shows both Users and Roles for a viewer with both permissions", () => {
    mockedUseAuth.mockReturnValue(
      authWithRoles(["STAFF"], ["users.manage", "roles.manage"])
    )
    renderWithProviders(<SidebarNav />)
    expect(screen.getByText("Users")).toBeInTheDocument()
    expect(screen.getByText("Roles")).toBeInTheDocument()
    expect(screen.getByText("Settings")).toBeInTheDocument()
  })
})

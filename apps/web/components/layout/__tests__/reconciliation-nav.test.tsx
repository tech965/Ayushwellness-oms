import { describe, expect, it, vi } from "vitest"
import { screen } from "@testing-library/react"

import { SidebarNav } from "@/components/layout/sidebar-nav"
import { useAuth } from "@/lib/auth-context"
import { navGroups } from "@/lib/navigation"
import { renderWithProviders } from "@/test-utils/render-with-providers"

vi.mock("next/navigation", () => ({
  usePathname: () => "/reconciliation/cod",
}))
vi.mock("@/lib/auth-context", () => ({ useAuth: vi.fn() }))

function auth(permissionCodes: string[]) {
  const permissions = new Set(permissionCodes)
  return {
    user: {
      id: "1",
      name: "T",
      email: "t@example.com",
      phone: null,
      is_active: true,
      is_superuser: false,
      roles: ["ADMIN"],
      permissions: permissionCodes,
    },
    permissions,
    isLoading: false,
    hasPermission: (code: string) => permissions.has(code),
    hasRole: (role: string) => role === "ADMIN",
    logout: vi.fn(),
  }
}

const allItems = navGroups.flatMap((g) => g.items)

describe("Reconciliation navigation", () => {
  it("has ONE top-level Reconciliation entry with the two remittance modules under it", () => {
    const reconciliation = allItems.filter((i) => i.label === "Reconciliation")
    expect(reconciliation).toHaveLength(1)
    expect(reconciliation[0].href).toBe("/reconciliation")
    expect(reconciliation[0].children?.map((c) => [c.label, c.href])).toEqual([
      ["COD Remittance Reconciliation", "/reconciliation/cod"],
      ["Prepaid Remittance Reconciliation", "/reconciliation/prepaid"],
    ])
  })

  it("keeps Shiprocket Wallet as a separate, unchanged entry", () => {
    const wallet = allItems.filter((i) => i.label === "Shiprocket Wallet")
    expect(wallet).toHaveLength(1)
    expect(wallet[0]).toMatchObject({
      href: "/shiprocket-wallet",
      permission: "analytics.read",
    })
    expect(wallet[0].children).toBeUndefined()
    const hrefs = allItems.flatMap((i) => [
      i.href,
      ...(i.children ?? []).map((c) => c.href),
    ])
    expect(new Set(hrefs).size).toBe(hrefs.length) // no duplicate routes
  })

  it("renders the sub-pages indented under Reconciliation, with the current one highlighted", () => {
    vi.mocked(useAuth).mockReturnValue(auth(["reconciliation.read", "analytics.read"]))
    renderWithProviders(<SidebarNav />)
    expect(screen.getByRole("link", { name: "Reconciliation" })).toHaveAttribute(
      "href",
      "/reconciliation"
    )
    const cod = screen.getByRole("link", { name: "COD Remittance Reconciliation" })
    expect(cod).toHaveAttribute("href", "/reconciliation/cod")
    expect(cod).toHaveAttribute("aria-current", "page")
    expect(
      screen.getByRole("link", { name: "Prepaid Remittance Reconciliation" })
    ).toHaveAttribute("href", "/reconciliation/prepaid")
    // The parent is not highlighted while a sub-page is open.
    expect(screen.getByRole("link", { name: "Reconciliation" })).not.toHaveAttribute(
      "aria-current"
    )
    expect(screen.getByRole("link", { name: "Shiprocket Wallet" })).toHaveAttribute(
      "href",
      "/shiprocket-wallet"
    )
  })

  it("hides the remittance sub-pages from a user without reconciliation.read", () => {
    vi.mocked(useAuth).mockReturnValue(auth(["analytics.read"]))
    renderWithProviders(<SidebarNav />)
    expect(screen.getByRole("link", { name: "Reconciliation" })).toBeInTheDocument()
    expect(
      screen.queryByRole("link", { name: "COD Remittance Reconciliation" })
    ).not.toBeInTheDocument()
    expect(
      screen.queryByRole("link", { name: "Prepaid Remittance Reconciliation" })
    ).not.toBeInTheDocument()
    expect(screen.getByRole("link", { name: "Shiprocket Wallet" })).toBeInTheDocument()
  })
})

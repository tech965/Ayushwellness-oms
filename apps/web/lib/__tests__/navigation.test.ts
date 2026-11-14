import { describe, expect, it } from "vitest"

import { navGroups } from "@/lib/navigation"

describe("navigation — Marketplace Management", () => {
  const group = navGroups.find((g) => g.label === "Marketplace Management")

  it("adds a separate Marketplace Management nav group", () => {
    expect(group).toBeDefined()
  })

  it("includes every item the requirement asks for, each gated on marketplace.read", () => {
    const labels = group?.items.map((i) => i.label)
    expect(labels).toEqual([
      "Marketplace Dashboard",
      "All Marketplace Orders",
      "Amazon Orders",
      "Flipkart Orders",
      "Meesho Orders",
      "Marketplace Integrations",
      "Marketplace Reports",
    ])
    for (const item of group?.items ?? []) {
      expect(item.permission).toBe("marketplace.read")
    }
  })

  it("never touches the existing Commerce -> Orders (Shopify) nav item", () => {
    const commerce = navGroups.find((g) => g.label === "Commerce")
    const ordersItem = commerce?.items.find((i) => i.label === "Orders")
    expect(ordersItem?.href).toBe("/orders")
    expect(ordersItem?.permission).toBeUndefined()
  })
})

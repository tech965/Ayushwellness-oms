import { describe, expect, it } from "vitest"

import { fulfillmentNavGroups, navGroups } from "@/lib/navigation"

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

describe("navigation — Shipment Analytics (SOW Modules 2-4)", () => {
  const group = navGroups.find((g) => g.label === "Shipment Analytics")

  it("groups the three modules under their exact SOW names, once each", () => {
    expect(group?.items.map((i) => [i.label, i.href])).toEqual([
      ["Shipment Tracking & Status Analytics", "/shipment-tracking"],
      ["Fulfilment & Delivery Performance", "/fulfilment-performance"],
      ["Shipping Partner Performance", "/shipping-partners"],
    ])
    for (const item of group?.items ?? []) {
      expect(item.permission).toBe("shipments.read")
    }
  })

  it("has no duplicate hrefs anywhere in the admin sidebar", () => {
    const hrefs = navGroups.flatMap((g) => g.items.map((i) => i.href))
    expect(new Set(hrefs).size).toBe(hrefs.length)
  })

  it("the old Marketplaces group is merged into Intelligence without losing its routes", () => {
    expect(navGroups.find((g) => g.label === "Marketplaces")).toBeUndefined()
    const hrefs = navGroups
      .find((g) => g.label === "Intelligence")
      ?.items.map((i) => i.href)
    expect(hrefs).toContain("/marketplaces/amazon/sales-analytics")
    expect(hrefs).toContain("/marketplaces/flipkart/orders")
  })

  it("Fulfillment role keeps Modules 2 and 3, gated on shipments.read", () => {
    const items = fulfillmentNavGroups.flatMap((g) => g.items)
    for (const href of ["/shipment-tracking", "/fulfilment-performance"]) {
      expect(items.find((i) => i.href === href)?.permission).toBe("shipments.read")
    }
  })
})

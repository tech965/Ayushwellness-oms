import { describe, expect, it } from "vitest"

import { buildPaymentDrilldownHref } from "@/lib/build-payment-drilldown-href"

describe("buildPaymentDrilldownHref", () => {
  it("omits provider entirely when All providers is selected (Total Payments card)", () => {
    const href = buildPaymentDrilldownHref({ provider: undefined })
    expect(href).toBe("/payments?")
    expect(href).not.toContain("provider=")
  })

  it("never hardcodes provider=cashfree regardless of the current filter", () => {
    const href = buildPaymentDrilldownHref({ provider: "" })
    expect(href).not.toContain("cashfree")
  })

  it("mirrors whatever provider is currently selected", () => {
    const href = buildPaymentDrilldownHref({ provider: "shopify" })
    expect(href).toContain("provider=shopify")
  })

  it("mirrors a selected cashfree provider too, not just shopify", () => {
    const href = buildPaymentDrilldownHref({ provider: "cashfree" })
    expect(href).toContain("provider=cashfree")
  })

  it("adds the extra status param for the Paid/Pending/Failed cards", () => {
    const href = buildPaymentDrilldownHref({ provider: undefined }, { status: "paid" })
    expect(href).toContain("status=paid")
    expect(href).not.toContain("provider=")
  })

  it("carries the current date range through unchanged", () => {
    const href = buildPaymentDrilldownHref({
      provider: undefined,
      date_from: "2026-08-01T00:00:00.000Z",
      date_to: "2026-08-31T23:59:59.999Z",
    })
    expect(href).toContain("date_from=2026-08-01T00%3A00%3A00.000Z")
    expect(href).toContain("date_to=2026-08-31T23%3A59%3A59.999Z")
  })

  it("combines a selected provider with a status drill-down (Failed card, Cashfree selected)", () => {
    const href = buildPaymentDrilldownHref({ provider: "cashfree" }, { status: "failed" })
    expect(href).toContain("provider=cashfree")
    expect(href).toContain("status=failed")
  })
})

import { describe, expect, it } from "vitest"

import { formatStatusLabel, getStatusTone } from "@/lib/status-styles"

describe("formatStatusLabel", () => {
  it("converts snake_case to Title Case", () => {
    expect(formatStatusLabel("partially_refunded")).toBe("Partially Refunded")
    expect(formatStatusLabel("delivered")).toBe("Delivered")
  })
})

describe("getStatusTone", () => {
  it("maps known statuses to the expected tone", () => {
    expect(getStatusTone("order", "delivered")).toBe("success")
    expect(getStatusTone("order", "cancelled")).toBe("danger")
    expect(getStatusTone("shipment_delay", "on_time")).toBe("success")
    expect(getStatusTone("shipment_delay", "delayed")).toBe("warning")
  })

  it("falls back to neutral for an unrecognized status", () => {
    expect(getStatusTone("order", "not_a_real_status")).toBe("neutral")
  })
})

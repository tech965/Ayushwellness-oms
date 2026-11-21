import { describe, expect, it } from "vitest"

import { formatRate } from "@/lib/format"

describe("formatRate", () => {
  it("is — for a zero denominator, whatever the rate", () => {
    expect(formatRate(0, 0)).toBe("—")
    expect(formatRate(100, 0)).toBe("—")
  })
  it("keeps a genuine 0% when there is a denominator", () => {
    expect(formatRate(0, 5)).toBe("0%")
  })
  it("renders non-zero rates", () => {
    expect(formatRate(33.33, 3)).toBe("33.33%")
  })
})

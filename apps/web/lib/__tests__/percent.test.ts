import { describe, expect, it } from "vitest"

import { formatPercent, formatShare, percentOf } from "@/lib/percent"

describe("percentOf", () => {
  it("computes a share of the denominator", () => {
    expect(percentOf(1, 4)).toBe(25)
    expect(percentOf(5144, 9493)).toBeCloseTo(54.19, 1)
  })

  it("is null (not 0, NaN or Infinity) for a zero or missing denominator", () => {
    expect(percentOf(5, 0)).toBeNull()
    expect(percentOf(0, 0)).toBeNull()
    expect(percentOf(5, undefined)).toBeNull()
    expect(percentOf(5, -3)).toBeNull()
    expect(percentOf(5, Number.NaN)).toBeNull()
  })

  it("is null for a missing numerator but a real 0% for a zero numerator", () => {
    expect(percentOf(null, 10)).toBeNull()
    expect(percentOf(undefined, 10)).toBeNull()
    expect(percentOf(0, 10)).toBe(0)
  })
})

describe("formatPercent / formatShare", () => {
  it("uses exactly one decimal place", () => {
    expect(formatPercent(35.1)).toBe("35.1%")
    expect(formatPercent(100)).toBe("100.0%")
    expect(formatPercent(0)).toBe("0.0%")
    expect(formatPercent(63.6364)).toBe("63.6%")
  })

  it("renders undefined values as a dash", () => {
    expect(formatPercent(null)).toBe("—")
    expect(formatPercent(undefined)).toBe("—")
    expect(formatPercent(Number.NaN)).toBe("—")
    expect(formatShare(3, 0)).toBe("—")
  })

  it("matches the worked examples", () => {
    const total = 9493
    expect(formatShare(5144, total)).toBe("54.2%")
    expect(formatShare(2346, total)).toBe("24.7%")
    expect(formatShare(869, total)).toBe("9.2%")
    expect(formatShare(1320, total)).toBe("13.9%")
    expect(formatShare(1106, total)).toBe("11.7%")
    expect(formatShare(1009, total)).toBe("10.6%")
    expect(formatShare(894, total)).toBe("9.4%")
  })
})

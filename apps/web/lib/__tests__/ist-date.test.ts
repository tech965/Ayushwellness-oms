import { describe, expect, it } from "vitest"

import { istDateKey } from "@/lib/ist-date"

describe("istDateKey", () => {
  it("formats an IST-afternoon UTC instant as that same calendar date", () => {
    // 2026-09-11T10:00:00Z = 2026-09-11T15:30:00 IST -- same calendar day.
    expect(istDateKey(new Date("2026-09-11T10:00:00Z"))).toBe("2026-09-11")
  })

  it("rolls a UTC-evening instant forward to the next IST calendar date", () => {
    // 2026-09-11T20:00:00Z = 2026-09-12T01:30:00 IST -- the next day.
    expect(istDateKey(new Date("2026-09-11T20:00:00Z"))).toBe("2026-09-12")
  })

  it("crosses the exact IST-midnight boundary (18:30 UTC) correctly", () => {
    // IST midnight = 18:30 UTC the previous day.
    expect(istDateKey(new Date("2026-09-11T18:29:59Z"))).toBe("2026-09-11")
    expect(istDateKey(new Date("2026-09-11T18:30:00Z"))).toBe("2026-09-12")
  })

  it("pads single-digit months and days", () => {
    expect(istDateKey(new Date("2026-01-05T10:00:00Z"))).toBe("2026-01-05")
  })
})

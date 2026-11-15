import { describe, expect, it } from "vitest"

import { allNavItems } from "@/lib/navigation"

describe("Administration navigation", () => {
  it("has exactly one Users management entry and no separate IMS Users page", () => {
    const userEntries = allNavItems.filter((i) => i.href.startsWith("/users"))
    expect(userEntries.map((i) => i.href)).toEqual(["/users"])
    expect(allNavItems.some((i) => /ims users/i.test(i.label))).toBe(false)
  })
})

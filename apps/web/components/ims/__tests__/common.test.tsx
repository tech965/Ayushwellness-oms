/// <reference types="vitest/globals" />

import { fmt, parseQty } from "@/lib/ims-utils"
import { imsNav } from "@/lib/ims-nav"

describe("IMS helpers", () => {
  it("accepts only positive whole-number quantities", () => {
    expect(parseQty("20")).toBe(20)
    expect(parseQty(" 7 ")).toBe(7)
    expect(parseQty("0")).toBeNull()
    expect(parseQty("-3")).toBeNull()
    expect(parseQty("1.5")).toBeNull()
    expect(parseQty("")).toBeNull()
  })

  it("formats quantities with Indian grouping and a dash for missing values", () => {
    expect(fmt(294000)).toBe("2,94,000")
    expect(fmt(null)).toBe("—")
  })

  it("only links to implemented IMS routes", () => {
    expect(imsNav.map((n) => n.href)).toEqual([
      "/ims",
      "/ims/products",
      "/ims/categories",
      "/ims/skus",
      "/ims/warehouses",
      "/ims/stock",
      "/ims/transfers",
      "/ims/returns",
      "/ims/packaging",
      "/ims/adjustments",
      "/ims/history",
      "/ims/reports",
      "/ims/settings",
    ])
  })
})

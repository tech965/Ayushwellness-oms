import { describe, expect, it } from "vitest"

import { roleHome } from "@/lib/role-home"

describe("roleHome", () => {
  it("sends IMS roles straight to the IMS dashboard", () => {
    expect(roleHome(["IMS_USER"])).toBe("/ims")
    expect(roleHome(["INVENTORY_STAFF"])).toBe("/ims")
    expect(roleHome(["INVENTORY_MANAGER"])).toBe("/ims")
  })

  it("leaves every other role on the existing flow", () => {
    expect(roleHome(["ADMIN"])).toBe("/dashboard")
    expect(roleHome(["OPERATIONS"])).toBe("/dashboard")
    expect(roleHome(["TELECALLER"])).toBe("/dashboard")
    expect(roleHome([])).toBe("/dashboard")
    expect(roleHome(undefined)).toBe("/dashboard")
  })

  it("keeps OMS section roles and superusers off /ims even if they also hold an IMS role", () => {
    expect(roleHome(["TELECALLER", "IMS_USER"])).toBe("/dashboard")
    expect(roleHome(["IMS_USER"], true)).toBe("/dashboard")
  })
})

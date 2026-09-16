import { describe, expect, it } from "vitest"

import { buildWhatsAppUrl, normalizePhoneForWhatsApp } from "@/lib/whatsapp"

describe("normalizePhoneForWhatsApp", () => {
  it("prepends 91 to a bare 10-digit Indian number", () => {
    expect(normalizePhoneForWhatsApp("9990000001")).toBe("919990000001")
  })

  it("strips a leading + and keeps the existing country code, never duplicating it", () => {
    expect(normalizePhoneForWhatsApp("+916304824438")).toBe("916304824438")
  })

  it("strips spaces, dashes, and brackets", () => {
    expect(normalizePhoneForWhatsApp("+91 630-482 (4438)")).toBe("916304824438")
    expect(normalizePhoneForWhatsApp("630 482 4438")).toBe("916304824438")
  })

  it("returns null for a missing phone number", () => {
    expect(normalizePhoneForWhatsApp(null)).toBeNull()
    expect(normalizePhoneForWhatsApp(undefined)).toBeNull()
    expect(normalizePhoneForWhatsApp("")).toBeNull()
  })

  it("returns null for a number too short to be usable, rather than guessing", () => {
    expect(normalizePhoneForWhatsApp("12345")).toBeNull()
  })

  it("returns null for a string with no digits at all", () => {
    expect(normalizePhoneForWhatsApp("N/A")).toBeNull()
  })
})

describe("buildWhatsAppUrl", () => {
  it("builds the exact wa.me URL from the requirement's own example", () => {
    expect(buildWhatsAppUrl("+916304824438")).toBe("https://wa.me/916304824438")
  })

  it("builds a wa.me URL for a bare 10-digit number with the 91 prefix added", () => {
    expect(buildWhatsAppUrl("9990000001")).toBe("https://wa.me/919990000001")
  })

  it("never pre-fills a message — no query string on the URL", () => {
    const url = buildWhatsAppUrl("+916304824438")
    expect(url).not.toContain("?")
    expect(url).not.toContain("text=")
  })

  it("returns null instead of a broken URL when the phone number is missing", () => {
    expect(buildWhatsAppUrl(null)).toBeNull()
    expect(buildWhatsAppUrl("")).toBeNull()
  })
})

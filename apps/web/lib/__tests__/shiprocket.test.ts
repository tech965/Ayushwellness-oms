import { afterEach, describe, expect, it, vi } from "vitest"

import { SHIPROCKET_READY_TO_SHIP_URL, copyToClipboard } from "@/lib/shiprocket"

describe("SHIPROCKET_READY_TO_SHIP_URL", () => {
  it("is always plain -- no query string, per Shiprocket support's confirmation that a deep-link filter param isn't supported", () => {
    expect(SHIPROCKET_READY_TO_SHIP_URL).toBe("https://app.shiprocket.in/seller/orders/readytoship")
    expect(SHIPROCKET_READY_TO_SHIP_URL).not.toContain("?")
  })
})

describe("copyToClipboard", () => {
  afterEach(() => {
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
  })

  it("writes the text and resolves true on success", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    vi.stubGlobal("navigator", { clipboard: { writeText } })

    const result = await copyToClipboard("1576398335")

    expect(writeText).toHaveBeenCalledWith("1576398335")
    expect(result).toBe(true)
  })

  it("resolves false instead of throwing when the clipboard API rejects (e.g. permission denied)", async () => {
    const writeText = vi.fn().mockRejectedValue(new Error("denied"))
    vi.stubGlobal("navigator", { clipboard: { writeText } })

    const result = await copyToClipboard("1576398335")

    expect(result).toBe(false)
  })

  it("resolves false instead of throwing when clipboard is entirely unavailable", async () => {
    vi.stubGlobal("navigator", {})

    const result = await copyToClipboard("1576398335")

    expect(result).toBe(false)
  })
})

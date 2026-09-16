import { afterEach, describe, expect, it, vi } from "vitest"
import { screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { WhatsAppButton } from "@/components/shared/whatsapp-button"
import { renderWithProviders } from "@/test-utils/render-with-providers"

describe("WhatsAppButton", () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it("renders when a valid customer phone number exists", () => {
    renderWithProviders(<WhatsAppButton phone="+916304824438" />)
    expect(
      screen.getByRole("button", { name: "Open WhatsApp chat with customer" })
    ).toBeInTheDocument()
  })

  it("opens the correct wa.me URL in a new tab when clicked, without sending a message", async () => {
    const user = userEvent.setup()
    const openSpy = vi.spyOn(window, "open").mockImplementation(() => null)

    renderWithProviders(<WhatsAppButton phone="+916304824438" />)
    await user.click(screen.getByRole("button", { name: "Open WhatsApp chat with customer" }))

    expect(openSpy).toHaveBeenCalledWith(
      "https://wa.me/916304824438",
      "_blank",
      "noopener,noreferrer"
    )
  })

  it("normalizes a bare 10-digit Indian number to the +91/country-code form", async () => {
    const user = userEvent.setup()
    const openSpy = vi.spyOn(window, "open").mockImplementation(() => null)

    renderWithProviders(<WhatsAppButton phone="9990000001" />)
    await user.click(screen.getByRole("button", { name: "Open WhatsApp chat with customer" }))

    expect(openSpy).toHaveBeenCalledWith(
      "https://wa.me/919990000001",
      "_blank",
      "noopener,noreferrer"
    )
  })

  it("does not duplicate the country code for a number that already has +91", async () => {
    const user = userEvent.setup()
    const openSpy = vi.spyOn(window, "open").mockImplementation(() => null)

    renderWithProviders(<WhatsAppButton phone="+919991234567" />)
    await user.click(screen.getByRole("button", { name: "Open WhatsApp chat with customer" }))

    const [url] = openSpy.mock.calls[0]
    expect(url).toBe("https://wa.me/919991234567")
    expect(url).not.toContain("9191")
  })

  it("renders nothing (never a broken link) when the phone number is missing", () => {
    const { container } = renderWithProviders(<WhatsAppButton phone={null} />)
    expect(container).toBeEmptyDOMElement()
    expect(screen.queryByRole("button")).not.toBeInTheDocument()
  })

  it("renders nothing for an unusably short phone number", () => {
    renderWithProviders(<WhatsAppButton phone="123" />)
    expect(screen.queryByRole("button")).not.toBeInTheDocument()
  })
})

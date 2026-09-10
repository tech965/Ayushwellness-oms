import { afterEach, describe, expect, it, vi } from "vitest"
import { screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { renderWithProviders } from "@/test-utils/render-with-providers"
import { ShiprocketOrderIdDialog } from "@/components/fulfillment/shiprocket-order-id-dialog"
import { toast } from "sonner"

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn() },
}))

/** `navigator.clipboard` must be stubbed AFTER `userEvent.setup()` -- that
 * call installs its own clipboard emulation, which would otherwise
 * clobber a stub set beforehand.
 */
function mockClipboard(impl: () => Promise<void> = () => Promise.resolve()) {
  const writeText = vi.fn(impl)
  Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true })
  return writeText
}

describe("ShiprocketOrderIdDialog", () => {
  afterEach(() => {
    vi.clearAllMocks()
  })

  it("displays the resolved Shiprocket order id in a read-only, selectable field", () => {
    renderWithProviders(
      <ShiprocketOrderIdDialog open onOpenChange={vi.fn()} orderId="1574579431" />
    )

    const field = screen.getByRole("textbox", { name: "Shiprocket Order ID" }) as HTMLInputElement
    expect(field.value).toBe("1574579431")
    expect(field.readOnly).toBe(true)
  })

  it("renders nothing when closed", () => {
    renderWithProviders(
      <ShiprocketOrderIdDialog open={false} onOpenChange={vi.fn()} orderId="1574579431" />
    )
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument()
  })

  it("copies the id directly from the Copy Order ID button's own click, with nothing else in that handler", async () => {
    const user = userEvent.setup()
    const writeText = mockClipboard()

    renderWithProviders(
      <ShiprocketOrderIdDialog open onOpenChange={vi.fn()} orderId="1574579431" />
    )

    // Not copied just by opening the dialog -- only the button click does.
    expect(writeText).not.toHaveBeenCalled()

    await user.click(screen.getByRole("button", { name: "Copy Order ID" }))

    expect(writeText).toHaveBeenCalledWith("1574579431")
    expect(toast.success).toHaveBeenCalledWith("Shiprocket Order ID 1574579431 copied.")
  })

  it("keeps the id selected and visible when the Clipboard API fails, so a manual Ctrl+C still works", async () => {
    const user = userEvent.setup()
    mockClipboard(() => Promise.reject(new DOMException("Document is not focused.")))

    renderWithProviders(
      <ShiprocketOrderIdDialog open onOpenChange={vi.fn()} orderId="1574579431" />
    )

    const field = screen.getByRole("textbox", { name: "Shiprocket Order ID" }) as HTMLInputElement
    await user.click(screen.getByRole("button", { name: "Copy Order ID" }))

    expect(toast.warning).toHaveBeenCalledWith(
      "Couldn't copy automatically.",
      expect.anything()
    )
    // Still there, still holding the real id, still read-only -- Ctrl+C
    // on the now-selected field always works as a fallback.
    expect(field).toBeInTheDocument()
    expect(field.value).toBe("1574579431")
    expect(field.readOnly).toBe(true)
  })

  it("disables the Copy Order ID button while no id is resolved yet", () => {
    renderWithProviders(<ShiprocketOrderIdDialog open onOpenChange={vi.fn()} orderId={null} />)
    expect(screen.getByRole("button", { name: "Copy Order ID" })).toBeDisabled()
  })
})

import { afterEach, describe, expect, it, vi } from "vitest"
import { screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { renderWithProviders } from "@/test-utils/render-with-providers"
import { ProcessShipmentDialog } from "@/components/fulfillment/process-shipment-dialog"
import type { ProcessExistingShipmentResult } from "@/types/shipment"

const READY_TO_SHIP_URL = "https://app.shiprocket.in/seller/orders/readytoship"

function result(overrides: Partial<ProcessExistingShipmentResult> = {}): ProcessExistingShipmentResult {
  return {
    order_id: "order-1",
    order_number: "OMS-0001",
    status: "success",
    shiprocket_shipment_id: "7001",
    shiprocket_order_id: "1900007001",
    awb: "AWB90001",
    courier_name: "Delhivery",
    reason: null,
    ...overrides,
  }
}

describe("ProcessShipmentDialog", () => {
  let openSpy: ReturnType<typeof vi.spyOn>
  let clipboardSpy: ReturnType<typeof vi.fn>

  afterEach(() => {
    openSpy?.mockRestore()
    vi.clearAllMocks()
  })

  function setUp() {
    openSpy = vi.spyOn(window, "open").mockReturnValue(null)
    clipboardSpy = vi.fn()
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText: clipboardSpy },
      configurable: true,
    })
  }

  it("shows a processing state while pending", () => {
    setUp()
    renderWithProviders(
      <ProcessShipmentDialog open onOpenChange={vi.fn()} isPending result={null} />
    )
    expect(screen.getByText(/Processing/i)).toBeInTheDocument()
  })

  it("shows courier + AWB on success", () => {
    setUp()
    renderWithProviders(
      <ProcessShipmentDialog
        open
        onOpenChange={vi.fn()}
        isPending={false}
        result={result({ status: "success", courier_name: "Delhivery", awb: "AWB90001" })}
      />
    )
    expect(screen.getByText(/Courier: Delhivery/i)).toBeInTheDocument()
    expect(screen.getByText(/AWB: AWB90001/i)).toBeInTheDocument()
  })

  it("shows the already-processed (skipped) state with the existing AWB", () => {
    setUp()
    renderWithProviders(
      <ProcessShipmentDialog
        open
        onOpenChange={vi.fn()}
        isPending={false}
        result={result({ status: "skipped", awb: "EXISTING-AWB", reason: "AWB already assigned." })}
      />
    )
    expect(screen.getByText(/already has AWB EXISTING-AWB/i)).toBeInTheDocument()
  })

  it("shows the exact failure reason, never hiding it", () => {
    setUp()
    renderWithProviders(
      <ProcessShipmentDialog
        open
        onOpenChange={vi.fn()}
        isPending={false}
        result={result({
          status: "failed",
          awb: null,
          courier_name: null,
          reason: "Courier serviceability check failed.",
        })}
      />
    )
    expect(screen.getByText(/failed — Courier serviceability check failed\./i)).toBeInTheDocument()
  })

  it("opens the plain Ready to Ship page, with no order_ids query parameter and no clipboard access", async () => {
    setUp()
    const user = userEvent.setup()
    renderWithProviders(
      <ProcessShipmentDialog open onOpenChange={vi.fn()} isPending={false} result={result()} />
    )

    await user.click(screen.getByRole("button", { name: /^Open Shiprocket Ready to Ship$/i }))

    expect(openSpy).toHaveBeenCalledWith(READY_TO_SHIP_URL, "_blank", "noopener,noreferrer")
    const [openedUrl] = openSpy.mock.calls[0]
    expect(String(openedUrl)).not.toContain("?")
    expect(String(openedUrl)).not.toContain("order_ids")
    expect(clipboardSpy).not.toHaveBeenCalled()
  })

  it("renders nothing when closed", () => {
    setUp()
    renderWithProviders(
      <ProcessShipmentDialog open={false} onOpenChange={vi.fn()} isPending={false} result={null} />
    )
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument()
  })
})

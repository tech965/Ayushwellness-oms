import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { renderWithProviders } from "@/test-utils/render-with-providers"
import ShipmentStaffOrdersPage from "@/app/(dashboard)/shipment-staff/orders/page"
import {
  useMyConfirmedOrders,
  useProcessExistingShipmentsForMyScope,
} from "@/services/shipment-staff"
import type { ProcessExistingShipmentResult } from "@/types/shipment"

const mockPush = vi.fn()
const READY_TO_SHIP_URL = "https://app.shiprocket.in/seller/orders/readytoship"

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockPush, replace: vi.fn(), back: vi.fn() }),
  usePathname: () => "/shipment-staff/orders",
  useSearchParams: () => new URLSearchParams(),
}))

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn() },
}))

vi.mock("@/services/shipment-staff", () => ({
  useMyConfirmedOrders: vi.fn(),
  useProcessExistingShipmentsForMyScope: vi.fn(),
}))

const mockedUseMyConfirmedOrders = vi.mocked(useMyConfirmedOrders)
const mockedUseProcessExistingShipmentsForMyScope = vi.mocked(
  useProcessExistingShipmentsForMyScope
)

type ProcessOpts = {
  onSuccess?: (r: {
    processed_count: number
    skipped_count: number
    failed_count: number
    results: ProcessExistingShipmentResult[]
  }) => void
  onError?: (e: unknown) => void
}

/** Default: the order's Shiprocket shipment can't be located -- individual
 * tests override `mutate` for the success/skipped paths.
 */
function mockProcessShipments(
  impl: (orderIds: string[], opts?: ProcessOpts) => void = (orderIds, opts) =>
    opts?.onSuccess?.({
      processed_count: 0,
      skipped_count: 0,
      failed_count: orderIds.length,
      results: orderIds.map((id) => ({
        order_id: id,
        order_number: null,
        status: "failed",
        shiprocket_shipment_id: null,
        shiprocket_order_id: null,
        awb: null,
        courier_name: null,
        reason: "Existing Shiprocket order could not be located for this order.",
      })),
    })
) {
  const mutate = vi.fn(impl)
  mockedUseProcessExistingShipmentsForMyScope.mockReturnValue({
    mutate,
    isPending: false,
  } as unknown as ReturnType<typeof useProcessExistingShipmentsForMyScope>)
  return mutate
}

const ROW = {
  order_id: "order-1",
  order_number: "OMS-0001",
  customer_name: "Alice",
  customer_phone: "9990000001",
  item_summary: "Ashwagandha",
  total_amount: "499.00",
  payment_type: "cod",
  confirmed_at: "2026-09-01T00:00:00Z",
  confirmed_by_telecaller_id: "tc-1",
  confirmed_by_telecaller_name: "Sourabh",
  shipment_id: null,
  shipment_status: null,
  shopify_sync_status: null,
  shiprocket_order_id: "1576398335",
  awb: null,
  courier_name: null,
}

describe("ShipmentStaffOrdersPage", () => {
  let openSpy: ReturnType<typeof vi.spyOn>
  let clipboardSpy: ReturnType<typeof vi.fn>

  beforeEach(() => {
    openSpy = vi.spyOn(window, "open").mockReturnValue(null)
    // No clipboard dependency exists for this workflow -- stubbed only to
    // detect an unexpected call, never relied on for the flow to work.
    clipboardSpy = vi.fn()
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText: clipboardSpy },
      configurable: true,
    })
    mockProcessShipments()
  })

  afterEach(() => {
    openSpy.mockRestore()
    vi.clearAllMocks()
  })

  it("lists only this Shipment Staff user's scoped confirmed orders, with no telecaller filter", async () => {
    const user = userEvent.setup()
    const mutate = mockProcessShipments()
    mockedUseMyConfirmedOrders.mockReturnValue({
      isLoading: false,
      isError: false,
      error: null,
      data: {
        data: [ROW],
        meta: { page: 1, page_size: 20, total_items: 1, total_pages: 1 },
      },
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useMyConfirmedOrders>)

    renderWithProviders(<ShipmentStaffOrdersPage />)

    expect(screen.getByText("Orders Need Shipment")).toBeInTheDocument()
    expect(screen.getByText("Alice")).toBeInTheDocument()
    expect(screen.getByText("Sourabh")).toBeInTheDocument()
    // Unlike the Fulfillment/Admin queue, there is no telecaller filter --
    // every row already belongs to this Shipment Staff user's own scope.
    expect(screen.queryByText("All telecallers")).not.toBeInTheDocument()

    // "Ship Order" calls the scoped process-shipment endpoint with this
    // order's id -- never a Shiprocket create-shipment API call from
    // here (same rule as the Fulfillment/Admin queue's
    // `ShipmentActionCell`).
    await user.click(screen.getByRole("button", { name: /^Ship Order$/i }))
    expect(mutate).toHaveBeenCalledWith(["order-1"], expect.anything())
  })

  it("shows the unavailable reason in the dialog when no existing Shiprocket shipment can be resolved", async () => {
    const user = userEvent.setup()
    mockedUseMyConfirmedOrders.mockReturnValue({
      isLoading: false,
      isError: false,
      error: null,
      data: {
        data: [{ ...ROW, shiprocket_order_id: null }],
        meta: { page: 1, page_size: 20, total_items: 1, total_pages: 1 },
      },
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useMyConfirmedOrders>)

    renderWithProviders(<ShipmentStaffOrdersPage />)

    await user.click(screen.getByRole("button", { name: /^Ship Order$/i }))

    const dialog = await screen.findByRole("dialog")
    expect(
      within(dialog).getByText(/Existing Shiprocket order could not be located/i)
    ).toBeInTheDocument()
    expect(clipboardSpy).not.toHaveBeenCalled()
  })

  it("shows the processing state, then courier + AWB on success", async () => {
    const user = userEvent.setup()
    mockProcessShipments((orderIds, opts) =>
      opts?.onSuccess?.({
        processed_count: 1,
        skipped_count: 0,
        failed_count: 0,
        results: orderIds.map((id) => ({
          order_id: id,
          order_number: "OMS-0001",
          status: "success",
          shiprocket_shipment_id: "7001",
          shiprocket_order_id: "1900007001",
          awb: "AWB90001",
          courier_name: "Delhivery",
          reason: null,
        })),
      })
    )
    mockedUseMyConfirmedOrders.mockReturnValue({
      isLoading: false,
      isError: false,
      error: null,
      data: {
        data: [{ ...ROW, shiprocket_order_id: null }],
        meta: { page: 1, page_size: 20, total_items: 1, total_pages: 1 },
      },
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useMyConfirmedOrders>)

    renderWithProviders(<ShipmentStaffOrdersPage />)

    await user.click(screen.getByRole("button", { name: /^Ship Order$/i }))

    const dialog = await screen.findByRole("dialog")
    expect(within(dialog).getByText(/Courier: Delhivery/i)).toBeInTheDocument()
    expect(within(dialog).getByText(/AWB: AWB90001/i)).toBeInTheDocument()
    expect(clipboardSpy).not.toHaveBeenCalled()
  })

  it("shows the already-processed (skipped) state when the shipment already has an AWB", async () => {
    const user = userEvent.setup()
    mockProcessShipments((orderIds, opts) =>
      opts?.onSuccess?.({
        processed_count: 0,
        skipped_count: 1,
        failed_count: 0,
        results: orderIds.map((id) => ({
          order_id: id,
          order_number: "OMS-0001",
          status: "skipped",
          shiprocket_shipment_id: "7001",
          shiprocket_order_id: "1900007001",
          awb: "EXISTING-AWB",
          courier_name: "Delhivery",
          reason: "AWB already assigned.",
        })),
      })
    )
    mockedUseMyConfirmedOrders.mockReturnValue({
      isLoading: false,
      isError: false,
      error: null,
      data: {
        data: [{ ...ROW, shipment_id: "ship-1", shipment_status: "pending" }],
        meta: { page: 1, page_size: 20, total_items: 1, total_pages: 1 },
      },
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useMyConfirmedOrders>)

    renderWithProviders(<ShipmentStaffOrdersPage />)

    await user.click(screen.getByRole("button", { name: /^Process Shipment$/i }))

    const dialog = await screen.findByRole("dialog")
    expect(within(dialog).getByText(/already has AWB EXISTING-AWB/i)).toBeInTheDocument()
  })

  it("opens the plain Ready to Ship page from the dialog, with no order_ids query parameter", async () => {
    const user = userEvent.setup()
    mockedUseMyConfirmedOrders.mockReturnValue({
      isLoading: false,
      isError: false,
      error: null,
      data: {
        data: [{ ...ROW, shipment_id: "ship-1", shipment_status: "pending" }],
        meta: { page: 1, page_size: 20, total_items: 1, total_pages: 1 },
      },
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useMyConfirmedOrders>)

    renderWithProviders(<ShipmentStaffOrdersPage />)

    await user.click(screen.getByRole("button", { name: /^Process Shipment$/i }))
    const dialog = await screen.findByRole("dialog")

    await user.click(
      within(dialog).getByRole("button", { name: /^Open Shiprocket Ready to Ship$/i })
    )

    expect(openSpy).toHaveBeenCalledWith(READY_TO_SHIP_URL, "_blank", "noopener,noreferrer")
    const [openedUrl] = openSpy.mock.calls[0]
    expect(String(openedUrl)).not.toContain("?")
    expect(String(openedUrl)).not.toContain("order_ids")
    expect(mockPush).not.toHaveBeenCalled()
  })

  it("shows an empty state scoped to this user's own Telecallers", () => {
    mockedUseMyConfirmedOrders.mockReturnValue({
      isLoading: false,
      isError: false,
      error: null,
      data: { data: [], meta: { page: 1, page_size: 20, total_items: 0, total_pages: 0 } },
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useMyConfirmedOrders>)

    renderWithProviders(<ShipmentStaffOrdersPage />)

    expect(screen.getByText("No confirmed orders awaiting shipment")).toBeInTheDocument()
  })
})

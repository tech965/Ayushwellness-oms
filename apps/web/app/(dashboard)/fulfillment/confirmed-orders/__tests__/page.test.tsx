import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { renderWithProviders } from "@/test-utils/render-with-providers"
import FulfillmentConfirmedOrdersPage from "@/app/(dashboard)/fulfillment/confirmed-orders/page"
import { useProcessExistingShipments, useTelecallerConfirmedOrders } from "@/services/orders"
import { useRetryShopifySync } from "@/services/shipments"
import { useTeamTelecallers } from "@/services/team"
import type { ProcessExistingShipmentResult } from "@/types/shipment"

const mockPush = vi.fn()
const READY_TO_SHIP_URL = "https://app.shiprocket.in/seller/orders/readytoship"

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockPush, replace: vi.fn(), back: vi.fn() }),
  usePathname: () => "/fulfillment/confirmed-orders",
  useSearchParams: () => new URLSearchParams(),
}))

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn() },
}))

vi.mock("@/services/orders", () => ({
  useTelecallerConfirmedOrders: vi.fn(),
  useProcessExistingShipments: vi.fn(),
}))

vi.mock("@/services/shipments", () => ({
  useRetryShopifySync: vi.fn(),
}))

vi.mock("@/services/team", () => ({
  useTeamTelecallers: vi.fn(),
}))

const mockedUseTelecallerConfirmedOrders = vi.mocked(useTelecallerConfirmedOrders)
const mockedUseTeamTelecallers = vi.mocked(useTeamTelecallers)
const mockedUseProcessExistingShipments = vi.mocked(useProcessExistingShipments)
const mockedUseRetryShopifySync = vi.mocked(useRetryShopifySync)

const ROW = {
  id: "order-1",
  order_number: "AWL95408",
  customer_name: "Ranjit",
  customer_phone: "9990000001",
  item_summary: "Ayush Wellness Herbal Masala x1",
  total_amount: "1749.00",
  payment_type: "cod",
  payment_status: "paid",
  status: "confirmed",
  fulfillment_status: "unfulfilled",
  shipment_status: null as string | null,
  shipment_id: null as string | null,
  shopify_sync_status: null as string | null,
  shiprocket_order_id: null as string | null,
  confirmed_at: "2026-09-09T10:32:00Z",
  confirmed_by_telecaller_id: "tc-1",
  confirmed_by_telecaller_name: "Rahul Sharma",
}

function mockQuery(data: typeof ROW[] = [ROW]) {
  mockedUseTelecallerConfirmedOrders.mockReturnValue({
    isLoading: false,
    isError: false,
    error: null,
    data: {
      data,
      meta: { page: 1, page_size: 20, total_items: data.length, total_pages: 1 },
    },
    refetch: vi.fn(),
  } as unknown as ReturnType<typeof useTelecallerConfirmedOrders>)
  mockedUseTeamTelecallers.mockReturnValue({
    data: [{ telecaller_id: "tc-1", telecaller_name: "Rahul Sharma" }],
  } as unknown as ReturnType<typeof useTeamTelecallers>)
}

type ProcessOpts = {
  onSuccess?: (r: {
    processed_count: number
    skipped_count: number
    failed_count: number
    results: ProcessExistingShipmentResult[]
  }) => void
  onError?: (e: unknown) => void
}

function _notFoundResult(id: string): ProcessExistingShipmentResult {
  return {
    order_id: id,
    order_number: null,
    status: "failed",
    shiprocket_shipment_id: null,
    shiprocket_order_id: null,
    awb: null,
    courier_name: null,
    reason: "Existing Shiprocket order could not be located for this order.",
  }
}

/** Default: every order id "fails to locate" -- individual tests override
 * `mutate` for the success/skipped paths.
 */
function mockProcessShipments(
  impl: (orderIds: string[], opts?: ProcessOpts) => void = (orderIds, opts) => {
    const results = orderIds.map(_notFoundResult)
    opts?.onSuccess?.({
      processed_count: 0,
      skipped_count: 0,
      failed_count: results.length,
      results,
    })
  }
) {
  const mutate = vi.fn(impl)
  mockedUseProcessExistingShipments.mockReturnValue({
    mutate,
    isPending: false,
  } as unknown as ReturnType<typeof useProcessExistingShipments>)
  return mutate
}

function mockShipHooks(
  processImpl?: (orderIds: string[], opts?: ProcessOpts) => void
) {
  const mutate = mockProcessShipments(processImpl)
  mockedUseRetryShopifySync.mockReturnValue({
    mutate: vi.fn(),
    isPending: false,
  } as unknown as ReturnType<typeof useRetryShopifySync>)
  return mutate
}

describe("FulfillmentConfirmedOrdersPage", () => {
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
  })

  afterEach(() => {
    openSpy.mockRestore()
    vi.clearAllMocks()
  })

  it("shows the page title and subtitle", () => {
    mockQuery()
    mockShipHooks()
    renderWithProviders(<FulfillmentConfirmedOrdersPage />)
    expect(screen.getByText("Confirmed by Telecaller")).toBeInTheDocument()
    expect(screen.getByText("1 orders confirmed by Telecalling.")).toBeInTheDocument()
  })

  it("always shows the real Telecaller name, never a dash, when confirmed_by_telecaller_id is set", () => {
    mockQuery()
    mockShipHooks()
    renderWithProviders(<FulfillmentConfirmedOrdersPage />)
    expect(screen.getByText("Rahul Sharma")).toBeInTheDocument()
  })

  it("renders order/customer/items/amount/status columns from the example row", () => {
    mockQuery()
    mockShipHooks()
    renderWithProviders(<FulfillmentConfirmedOrdersPage />)
    expect(screen.getByText("AWL95408")).toBeInTheDocument()
    expect(screen.getByText("Ranjit")).toBeInTheDocument()
    expect(screen.getByText("Ayush Wellness Herbal Masala x1")).toBeInTheDocument()
  })

  it("calls the process-shipment endpoint with this order's id when Ship Order is clicked", async () => {
    const user = userEvent.setup()
    mockQuery([{ ...ROW, shiprocket_order_id: "1576398335" }])
    const mutate = mockShipHooks()

    renderWithProviders(<FulfillmentConfirmedOrdersPage />)

    await user.click(screen.getByRole("button", { name: /^Ship Order$/i }))
    expect(mutate).toHaveBeenCalledWith(["order-1"], expect.anything())
  })

  it("shows courier + AWB in the dialog on success, then opens the plain Ready to Ship page", async () => {
    const user = userEvent.setup()
    mockQuery([{ ...ROW, shiprocket_order_id: "1576398335" }])
    mockShipHooks((orderIds, opts) =>
      opts?.onSuccess?.({
        processed_count: 1,
        skipped_count: 0,
        failed_count: 0,
        results: orderIds.map((id) => ({
          order_id: id,
          order_number: "AWL95408",
          status: "success",
          shiprocket_shipment_id: "7001",
          shiprocket_order_id: "1900007001",
          awb: "AWB90001",
          courier_name: "Delhivery",
          reason: null,
        })),
      })
    )

    renderWithProviders(<FulfillmentConfirmedOrdersPage />)

    await user.click(screen.getByRole("button", { name: /^Ship Order$/i }))
    const dialog = await screen.findByRole("dialog")
    expect(within(dialog).getByText(/Courier: Delhivery/i)).toBeInTheDocument()
    expect(within(dialog).getByText(/AWB: AWB90001/i)).toBeInTheDocument()

    await user.click(
      within(dialog).getByRole("button", { name: /^Open Shiprocket Ready to Ship$/i })
    )
    expect(openSpy).toHaveBeenCalledWith(READY_TO_SHIP_URL, "_blank", "noopener,noreferrer")
    const [openedUrl] = openSpy.mock.calls[0]
    expect(String(openedUrl)).not.toContain("?")
    expect(clipboardSpy).not.toHaveBeenCalled()
  })

  it("shows the failure reason, never a Shiprocket create-shipment API call, when no shipment can be resolved", async () => {
    const user = userEvent.setup()
    mockQuery()
    mockShipHooks()

    renderWithProviders(<FulfillmentConfirmedOrdersPage />)

    await user.click(screen.getByRole("button", { name: /^Ship Order$/i }))
    const dialog = await screen.findByRole("dialog")
    expect(
      within(dialog).getByText(/Existing Shiprocket order could not be located/i)
    ).toBeInTheDocument()
  })

  it("hides Ship Order once the order is already shipped/in transit/delivered", () => {
    mockQuery([
      {
        ...ROW,
        status: "shipped",
        fulfillment_status: "fulfilled",
        shipment_status: "delivered",
        shipment_id: "ship-1",
      },
    ])
    mockShipHooks()
    renderWithProviders(<FulfillmentConfirmedOrdersPage />)

    expect(screen.getByText("AWL95408")).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: /^Ship Order$/i })).not.toBeInTheDocument()
    expect(screen.getByRole("button", { name: /^View$/i })).toBeInTheDocument()
  })

  it("navigates to the order detail page from the View action, without filtering by shipment status", async () => {
    const user = userEvent.setup()
    mockQuery()
    mockShipHooks()
    renderWithProviders(<FulfillmentConfirmedOrdersPage />)
    await user.click(screen.getByRole("button", { name: /^View$/i }))
    expect(mockPush).toHaveBeenCalledWith("/orders/order-1")
  })

  it("shows a shipped order (not just orders still needing shipment) -- this is a history view", () => {
    mockQuery([
      { ...ROW, status: "shipped", fulfillment_status: "fulfilled", shipment_status: "delivered" },
    ])
    mockShipHooks()
    renderWithProviders(<FulfillmentConfirmedOrdersPage />)
    expect(screen.getByText("AWL95408")).toBeInTheDocument()
  })

  it("bulk-selects orders, processes them in one call, and shows a per-order result -- never a create-shipment API call", async () => {
    const user = userEvent.setup()
    mockQuery()
    const mutate = mockShipHooks((orderIds, opts) =>
      opts?.onSuccess?.({
        processed_count: 1,
        skipped_count: 0,
        failed_count: 0,
        results: orderIds.map((id) => ({
          order_id: id,
          order_number: "AWL95408",
          status: "success",
          shiprocket_shipment_id: "7001",
          shiprocket_order_id: "1600000001",
          awb: "AWB1600001",
          courier_name: "Xpressbees",
          reason: null,
        })),
      })
    )

    renderWithProviders(<FulfillmentConfirmedOrdersPage />)

    const checkboxes = screen.getAllByRole("checkbox")
    await user.click(checkboxes[1])
    expect(screen.getByText("Selected 1 orders")).toBeInTheDocument()

    await user.click(screen.getByRole("button", { name: /^Process Shipment$/i }))
    expect(mutate).toHaveBeenCalledWith(["order-1"], expect.anything())

    const dialog = await screen.findByRole("alertdialog")
    expect(
      await within(dialog).findByText("Processed 1 of 1 selected orders")
    ).toBeInTheDocument()
    expect(within(dialog).getByText(/Courier: Xpressbees — AWB: AWB1600001/)).toBeInTheDocument()

    await user.click(
      within(dialog).getByRole("button", { name: /^Open Shiprocket Ready to Ship$/i })
    )
    expect(openSpy).toHaveBeenCalledWith(READY_TO_SHIP_URL, "_blank", "noopener,noreferrer")
    expect(clipboardSpy).not.toHaveBeenCalled()
  })

  it("bulk dialog shows the real reason for orders whose Shiprocket shipment could not be resolved", async () => {
    const user = userEvent.setup()
    mockQuery([ROW, { ...ROW, id: "order-2", order_number: "AWL95409" }])
    mockShipHooks((orderIds, opts) =>
      opts?.onSuccess?.({
        processed_count: 1,
        skipped_count: 0,
        failed_count: 1,
        results: [
          {
            order_id: orderIds[0],
            order_number: "AWL95408",
            status: "success",
            shiprocket_shipment_id: "7001",
            shiprocket_order_id: "1600000001",
            awb: "AWB1600001",
            courier_name: "Xpressbees",
            reason: null,
          },
          {
            order_id: orderIds[1],
            order_number: "AWL95409",
            status: "failed",
            shiprocket_shipment_id: null,
            shiprocket_order_id: null,
            awb: null,
            courier_name: null,
            reason: "Existing Shiprocket order could not be located for this order.",
          },
        ],
      })
    )

    renderWithProviders(<FulfillmentConfirmedOrdersPage />)

    const checkboxes = screen.getAllByRole("checkbox")
    await user.click(checkboxes[0]) // select-all header checkbox
    await user.click(screen.getByRole("button", { name: /^Process Shipment$/i }))

    const dialog = await screen.findByRole("alertdialog")
    expect(
      await within(dialog).findByText(/Existing Shiprocket order could not be located/i)
    ).toBeInTheDocument()
    expect(within(dialog).getByText("AWL95409")).toBeInTheDocument()
  })

  it("shows an empty state when nothing has been confirmed by Telecalling yet", () => {
    mockQuery([])
    mockShipHooks()
    renderWithProviders(<FulfillmentConfirmedOrdersPage />)
    expect(screen.getByText("No Telecaller-confirmed orders yet")).toBeInTheDocument()
  })
})

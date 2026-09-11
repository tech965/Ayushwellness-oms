import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { renderWithProviders } from "@/test-utils/render-with-providers"
import FulfillmentOrdersPage from "@/app/(dashboard)/fulfillment/orders/page"
import { useProcessExistingShipments } from "@/services/orders"
import { useShipmentQueue } from "@/services/shipment-queue"
import { useRetryShopifySync } from "@/services/shipments"
import { useTeamTelecallers } from "@/services/team"
import type { ProcessExistingShipmentResult } from "@/types/shipment"

const mockPush = vi.fn()
const READY_TO_SHIP_URL = "https://app.shiprocket.in/seller/orders/readytoship"

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockPush, replace: vi.fn(), back: vi.fn() }),
  usePathname: () => "/fulfillment/orders",
  useSearchParams: () => new URLSearchParams(),
}))

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn() },
}))

vi.mock("@/services/shipment-queue", () => ({
  useShipmentQueue: vi.fn(),
}))

vi.mock("@/services/orders", () => ({
  useProcessExistingShipments: vi.fn(),
}))

vi.mock("@/services/shipments", () => ({
  useRetryShopifySync: vi.fn(),
}))

vi.mock("@/services/team", () => ({
  useTeamTelecallers: vi.fn(),
}))

const mockedUseShipmentQueue = vi.mocked(useShipmentQueue)
const mockedUseTeamTelecallers = vi.mocked(useTeamTelecallers)
const mockedUseProcessExistingShipments = vi.mocked(useProcessExistingShipments)
const mockedUseRetryShopifySync = vi.mocked(useRetryShopifySync)

type ProcessOpts = {
  onSuccess?: (r: { processed_count: number; skipped_count: number; failed_count: number; results: ProcessExistingShipmentResult[] }) => void
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
 * `mutate` for the success/skipped paths. This hook now does the whole
 * resolve+assign in one server-side call -- no separate "locate" step on
 * the frontend anymore.
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
  },
  isPending = false
) {
  const mutate = vi.fn(impl)
  mockedUseProcessExistingShipments.mockReturnValue({
    mutate,
    isPending,
    reset: vi.fn(),
  } as unknown as ReturnType<typeof useProcessExistingShipments>)
  return mutate
}

function mockShipHooks() {
  const mutate = mockProcessShipments()
  mockedUseRetryShopifySync.mockReturnValue({
    mutate: vi.fn(),
    isPending: false,
  } as unknown as ReturnType<typeof useRetryShopifySync>)
  return mutate
}

const ROW = {
  order_id: "order-1",
  order_number: "OMS-0001",
  customer_name: "Alice",
  customer_phone: "9990000001",
  item_summary: "Ashwagandha x2",
  sku_summary: "ASH-001",
  total_quantity: 2,
  total_amount: "499.00",
  payment_type: "cod",
  payment_status: "pending",
  confirmed_at: "2026-09-01T00:00:00Z",
  confirmed_by_telecaller_id: "tc-1",
  confirmed_by_telecaller_name: "Sourabh",
  shipment_id: null as string | null,
  shipment_status: null as string | null,
  shopify_sync_status: null as string | null,
  shiprocket_order_id: null as string | null,
  awb: null,
  courier_name: null,
  shipping_address_validation_status: null as
    | "valid"
    | "ambiguous"
    | "junk"
    | "unknown"
    | null,
  shipping_address_validation_score: null as number | null,
  shipping_address_validation_reason: null as string | null,
}

function mockQueue(data = [ROW]) {
  mockedUseShipmentQueue.mockReturnValue({
    isLoading: false,
    isError: false,
    error: null,
    data: {
      data,
      meta: { page: 1, page_size: 20, total_items: data.length, total_pages: 1 },
    },
    refetch: vi.fn(),
  } as unknown as ReturnType<typeof useShipmentQueue>)
  mockedUseTeamTelecallers.mockReturnValue({
    data: [{ telecaller_id: "tc-1", telecaller_name: "Sourabh" }],
  } as unknown as ReturnType<typeof useTeamTelecallers>)
}

describe("FulfillmentOrdersPage", () => {
  let openSpy: ReturnType<typeof vi.spyOn>
  let clipboardSpy: ReturnType<typeof vi.fn>

  beforeEach(() => {
    openSpy = vi.spyOn(window, "open").mockReturnValue(null)
    // 7. No clipboard dependency exists for this workflow -- stubbed only
    // to detect an unexpected call, never relied on for the flow to work.
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

  it("calls the new process-shipment endpoint with this order's id when Ship Order is clicked", async () => {
    const user = userEvent.setup()
    mockQueue()
    const mutate = mockShipHooks()

    renderWithProviders(<FulfillmentOrdersPage />)

    await user.click(screen.getByRole("button", { name: /^Ship Order$/i }))

    expect(mutate).toHaveBeenCalledWith(["order-1"], expect.anything())
    expect(mockPush).not.toHaveBeenCalled()
    expect(clipboardSpy).not.toHaveBeenCalled()
  })

  it("shows the processing state, then courier + AWB on success", async () => {
    const user = userEvent.setup()
    mockQueue()
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
    mockedUseRetryShopifySync.mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useRetryShopifySync>)

    renderWithProviders(<FulfillmentOrdersPage />)

    await user.click(screen.getByRole("button", { name: /^Ship Order$/i }))

    const dialog = await screen.findByRole("dialog")
    expect(within(dialog).getByText(/Courier: Delhivery/i)).toBeInTheDocument()
    expect(within(dialog).getByText(/AWB: AWB90001/i)).toBeInTheDocument()
    expect(clipboardSpy).not.toHaveBeenCalled()
  })

  it("shows the already-processed (skipped) state when the shipment already has an AWB", async () => {
    const user = userEvent.setup()
    mockQueue([{ ...ROW, shipment_id: "ship-1", shipment_status: "pending" }])
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
    mockedUseRetryShopifySync.mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useRetryShopifySync>)

    renderWithProviders(<FulfillmentOrdersPage />)

    await user.click(screen.getByRole("button", { name: /^Process Shipment$/i }))

    const dialog = await screen.findByRole("dialog")
    expect(within(dialog).getByText(/already has AWB EXISTING-AWB/i)).toBeInTheDocument()
  })

  it("shows the failure reason when processing fails", async () => {
    const user = userEvent.setup()
    mockQueue()
    mockShipHooks()

    renderWithProviders(<FulfillmentOrdersPage />)

    await user.click(screen.getByRole("button", { name: /^Ship Order$/i }))

    const dialog = await screen.findByRole("dialog")
    expect(
      within(dialog).getByText(/Existing Shiprocket order could not be located/i)
    ).toBeInTheDocument()
  })

  it("shows a failure reason from a request-level error (never silently swallowed)", async () => {
    const user = userEvent.setup()
    mockQueue()
    mockProcessShipments((_orderIds, opts) =>
      opts?.onError?.(new Error("Shiprocket is not configured."))
    )
    mockedUseRetryShopifySync.mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useRetryShopifySync>)

    renderWithProviders(<FulfillmentOrdersPage />)

    await user.click(screen.getByRole("button", { name: /^Ship Order$/i }))

    const dialog = await screen.findByRole("dialog")
    expect(within(dialog).getByText(/Shiprocket is not configured\./i)).toBeInTheDocument()
  })

  it("opens the plain Ready to Ship page from the dialog, with no order_ids query parameter", async () => {
    const user = userEvent.setup()
    mockQueue()
    mockShipHooks()

    renderWithProviders(<FulfillmentOrdersPage />)

    await user.click(screen.getByRole("button", { name: /^Ship Order$/i }))
    const dialog = await screen.findByRole("dialog")

    await user.click(within(dialog).getByRole("button", { name: /^Open Shiprocket Ready to Ship$/i }))

    expect(openSpy).toHaveBeenCalledWith(READY_TO_SHIP_URL, "_blank", "noopener,noreferrer")
    const [openedUrl] = openSpy.mock.calls[0]
    expect(String(openedUrl)).not.toContain("?")
    expect(String(openedUrl)).not.toContain("order_ids")
  })

  it("does not open a second tab on a rapid double-click -- the dialog itself blocks re-clicking the button", async () => {
    const user = userEvent.setup()
    mockQueue()
    mockShipHooks()

    renderWithProviders(<FulfillmentOrdersPage />)

    const button = screen.getByRole("button", { name: /^Ship Order$/i })
    await user.click(button)
    await screen.findByRole("dialog")

    // The open dialog makes the underlying table inert -- a second click
    // on the same button can't even be delivered.
    expect(openSpy).not.toHaveBeenCalled()
  })

  it("processes every selected order in one bulk call and shows a per-order result summary", async () => {
    const user = userEvent.setup()
    mockQueue([ROW, { ...ROW, order_id: "order-2", order_number: "OMS-0002" }])
    mockProcessShipments((orderIds, opts) =>
      opts?.onSuccess?.({
        processed_count: 1,
        skipped_count: 0,
        failed_count: 1,
        results: [
          {
            order_id: orderIds[0],
            order_number: "OMS-0001",
            status: "success",
            shiprocket_shipment_id: "7001",
            shiprocket_order_id: "1900007001",
            awb: "AWB90001",
            courier_name: "Delhivery",
            reason: null,
          },
          {
            order_id: orderIds[1],
            order_number: "OMS-0002",
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
    mockedUseRetryShopifySync.mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useRetryShopifySync>)

    renderWithProviders(<FulfillmentOrdersPage />)

    const checkboxes = screen.getAllByRole("checkbox")
    await user.click(checkboxes[0]) // select-all header checkbox
    await user.click(screen.getByRole("button", { name: /^Process Shipment$/i }))

    const dialog = await screen.findByRole("alertdialog")
    expect(
      await within(dialog).findByText("Processed 1 of 2 selected orders")
    ).toBeInTheDocument()
    expect(within(dialog).getByText(/Courier: Delhivery — AWB: AWB90001/)).toBeInTheDocument()
    expect(
      within(dialog).getByText(/Existing Shiprocket order could not be located/i)
    ).toBeInTheDocument()

    await user.click(within(dialog).getByRole("button", { name: /^Open Shiprocket Ready to Ship$/i }))
    expect(openSpy).toHaveBeenCalledWith(READY_TO_SHIP_URL, "_blank", "noopener,noreferrer")
    expect(clipboardSpy).not.toHaveBeenCalled()
  })

  it("does not show the bulk toolbar when nothing is selected", () => {
    mockQueue()
    mockShipHooks()

    renderWithProviders(<FulfillmentOrdersPage />)

    expect(screen.queryByText(/^Selected /)).not.toBeInTheDocument()
  })

  it("shows an empty state when nothing is awaiting shipment", () => {
    mockQueue([])
    mockShipHooks()

    renderWithProviders(<FulfillmentOrdersPage />)

    expect(screen.getByText("No confirmed orders awaiting shipment")).toBeInTheDocument()
  })

  // This is the PRIMARY operational location for address validation --
  // staff must see a problem here, before clicking "Process Shipment",
  // without any extra request. Sourced straight off `ShipmentQueueRow`
  // (already loaded by `useShipmentQueue`), so this must render for
  // every row with zero additional queue-row calls.
  it("shows the Address Validation column so staff can spot a bad address before processing shipment", () => {
    mockQueue([
      { ...ROW, shipping_address_validation_status: "junk", shipping_address_validation_score: 24 },
      {
        ...ROW,
        order_id: "order-2",
        order_number: "OMS-0002",
        shipping_address_validation_status: "valid",
        shipping_address_validation_score: 95,
      },
    ])
    mockShipHooks()

    renderWithProviders(<FulfillmentOrdersPage />)

    expect(screen.getByText("24%")).toBeInTheDocument()
    expect(screen.getByText("Junk Address")).toBeInTheDocument()
    expect(screen.getByText("95%")).toBeInTheDocument()
    expect(screen.getByText("Valid Address")).toBeInTheDocument()
  })

  it("shows 'Validation pending' rather than blocking the page when an order has no stored validation yet", () => {
    mockQueue([
      { ...ROW, shipping_address_validation_status: null, shipping_address_validation_score: null },
    ])
    mockShipHooks()

    renderWithProviders(<FulfillmentOrdersPage />)

    expect(screen.getByText("Validation pending")).toBeInTheDocument()
  })
})

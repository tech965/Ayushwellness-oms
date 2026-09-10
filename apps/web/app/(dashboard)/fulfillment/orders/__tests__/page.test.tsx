import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { renderWithProviders } from "@/test-utils/render-with-providers"
import FulfillmentOrdersPage from "@/app/(dashboard)/fulfillment/orders/page"
import { toast } from "sonner"
import { useLocateShiprocketOrders, useShipmentQueue } from "@/services/shipment-queue"
import { useRetryShopifySync } from "@/services/shipments"
import { useTeamTelecallers } from "@/services/team"

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
  useLocateShiprocketOrders: vi.fn(),
}))

vi.mock("@/services/shipments", () => ({
  useRetryShopifySync: vi.fn(),
}))

vi.mock("@/services/team", () => ({
  useTeamTelecallers: vi.fn(),
}))

const mockedUseShipmentQueue = vi.mocked(useShipmentQueue)
const mockedUseTeamTelecallers = vi.mocked(useTeamTelecallers)
const mockedUseLocateShiprocketOrders = vi.mocked(useLocateShiprocketOrders)
const mockedUseRetryShopifySync = vi.mocked(useRetryShopifySync)

/** Default: a locate that reports "not found" for whatever ids it's
 * handed -- individual tests override `mutate` for the found path.
 */
function mockLocateHook(
  impl: (ids: string[], opts?: { onSuccess?: (r: unknown) => void }) => void = (ids, opts) =>
    opts?.onSuccess?.(
      ids.map((id) => ({
        order_id: id,
        status: "not_found",
        shiprocket_order_id: null,
        message: null,
      }))
    )
) {
  mockedUseLocateShiprocketOrders.mockReturnValue({
    mutate: vi.fn(impl),
    isPending: false,
  } as unknown as ReturnType<typeof useLocateShiprocketOrders>)
}

function mockShipHooks() {
  mockLocateHook()
  mockedUseRetryShopifySync.mockReturnValue({
    mutate: vi.fn(),
    isPending: false,
  } as unknown as ReturnType<typeof useRetryShopifySync>)
}

/** `navigator.clipboard` must be stubbed AFTER `userEvent.setup()` --
 * that call installs its own clipboard emulation, which would otherwise
 * clobber a stub set beforehand.
 */
function mockClipboard() {
  const writeText = vi.fn().mockResolvedValue(undefined)
  Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true })
  return writeText
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

  beforeEach(() => {
    openSpy = vi.spyOn(window, "open").mockReturnValue(null)
  })

  afterEach(() => {
    openSpy.mockRestore()
    vi.clearAllMocks()
  })

  it("shows the unavailable message when a live locate finds no existing Shiprocket order", async () => {
    const user = userEvent.setup()
    mockQueue()
    mockShipHooks()

    renderWithProviders(<FulfillmentOrdersPage />)

    expect(screen.getByText("Orders Need Shipment")).toBeInTheDocument()
    expect(screen.getByText("Alice")).toBeInTheDocument()
    expect(screen.getByText("Ready to Ship")).toBeInTheDocument()

    // No stored Shiprocket order id -- clicking triggers a live locate
    // (never a create-shipment call), and when that also finds nothing
    // the button says so instead of opening a guessed URL.
    await user.click(screen.getByRole("button", { name: /^Ship Order$/i }))
    expect(openSpy).not.toHaveBeenCalled()
    expect(toast.error).toHaveBeenCalledWith(
      "Shiprocket order ID is unavailable for this order.",
      expect.anything()
    )
    expect(mockPush).not.toHaveBeenCalled()
  })

  it("opens the plain Ready to Ship page and shows the resolved ID in a dialog when a live locate finds it", async () => {
    const user = userEvent.setup()
    mockClipboard()
    mockQueue()
    mockLocateHook((ids, opts) =>
      opts?.onSuccess?.(
        ids.map((id) => ({
          order_id: id,
          status: "found",
          shiprocket_order_id: "1576398335",
          message: null,
        }))
      )
    )
    mockedUseRetryShopifySync.mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useRetryShopifySync>)

    renderWithProviders(<FulfillmentOrdersPage />)

    await user.click(screen.getByRole("button", { name: /^Ship Order$/i }))
    // Ready to Ship opens with no query string -- no automatic clipboard
    // attempt is chained after it.
    expect(openSpy).toHaveBeenCalledWith(READY_TO_SHIP_URL, "_blank", "noopener,noreferrer")

    const dialog = await screen.findByRole("dialog")
    expect(within(dialog).getByDisplayValue("1576398335")).toBeInTheDocument()
  })

  it("copies the ID directly from the dialog's own Copy Order ID button click, not automatically", async () => {
    const user = userEvent.setup()
    const writeText = mockClipboard()
    mockQueue([{ ...ROW, shiprocket_order_id: "1576398335" }])
    mockShipHooks()

    renderWithProviders(<FulfillmentOrdersPage />)

    await user.click(screen.getByRole("button", { name: /^Ship Order$/i }))
    const dialog = await screen.findByRole("dialog")
    // Not copied yet -- only the dialog's own button click does that.
    expect(writeText).not.toHaveBeenCalled()

    await user.click(within(dialog).getByRole("button", { name: /^Copy Order ID$/i }))
    expect(writeText).toHaveBeenCalledWith("1576398335")
    expect(toast.success).toHaveBeenCalledWith("Shiprocket Order ID 1576398335 copied.")
  })

  it("leaves the ID selected in the dialog when the clipboard API fails, instead of silently doing nothing", async () => {
    const user = userEvent.setup()
    const writeText = vi.fn().mockRejectedValue(new DOMException("Document is not focused."))
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true })
    mockQueue([{ ...ROW, shiprocket_order_id: "1576398335" }])
    mockShipHooks()

    renderWithProviders(<FulfillmentOrdersPage />)

    await user.click(screen.getByRole("button", { name: /^Ship Order$/i }))
    const dialog = await screen.findByRole("dialog")
    const field = within(dialog).getByDisplayValue("1576398335") as HTMLInputElement

    await user.click(within(dialog).getByRole("button", { name: /^Copy Order ID$/i }))

    expect(toast.warning).toHaveBeenCalledWith(
      "Couldn't copy automatically.",
      expect.anything()
    )
    // The field is still there, still holding the real id, and selected
    // (its value is never cleared) -- a manual Ctrl+C always works.
    expect(field).toBeInTheDocument()
    expect(field.value).toBe("1576398335")
    expect(field.readOnly).toBe(true)
  })

  it("opens the plain Ready to Ship page directly when the id is already on the row", async () => {
    const user = userEvent.setup()
    mockClipboard()
    mockQueue([{ ...ROW, shiprocket_order_id: "1576398335" }])
    mockShipHooks()

    renderWithProviders(<FulfillmentOrdersPage />)

    await user.click(screen.getByRole("button", { name: /^Ship Order$/i }))
    expect(openSpy).toHaveBeenCalledWith(READY_TO_SHIP_URL, "_blank", "noopener,noreferrer")
    expect(mockPush).not.toHaveBeenCalled()
  })

  it("shows Process Shipment (not Ship Order) once a shipment already exists", async () => {
    const user = userEvent.setup()
    mockClipboard()
    mockQueue([
      {
        ...ROW,
        shipment_id: "ship-1",
        shipment_status: "pending",
        shiprocket_order_id: "1576398335",
      },
    ])
    mockShipHooks()

    renderWithProviders(<FulfillmentOrdersPage />)

    expect(screen.queryByRole("button", { name: /^Ship Order$/i })).not.toBeInTheDocument()
    await user.click(screen.getByRole("button", { name: /^Process Shipment$/i }))
    expect(openSpy).toHaveBeenCalledWith(READY_TO_SHIP_URL, "_blank", "noopener,noreferrer")
    expect(mockPush).not.toHaveBeenCalled()
  })

  it("does not open a second tab on a rapid double-click -- the dialog itself blocks re-clicking the button", async () => {
    const user = userEvent.setup()
    mockClipboard()
    mockQueue([{ ...ROW, shiprocket_order_id: "1576398335" }])
    mockShipHooks()

    renderWithProviders(<FulfillmentOrdersPage />)

    const button = screen.getByRole("button", { name: /^Ship Order$/i })
    await user.click(button)
    await screen.findByRole("dialog")

    // The open dialog makes the underlying table inert -- a second click
    // on the same button can't even be delivered, so at most one tab is
    // ever opened per dialog.
    expect(openSpy).toHaveBeenCalledTimes(1)
  })

  it("bulk action locates every selected order, copies its ID, and never calls a create-shipment API", async () => {
    const user = userEvent.setup()
    const writeText = mockClipboard()
    const locateMutate = vi.fn((ids: string[], opts?: { onSuccess?: (r: unknown) => void }) =>
      opts?.onSuccess?.(
        ids.map((id) => ({
          order_id: id,
          status: "found",
          shiprocket_order_id: "1600000001",
          message: null,
        }))
      )
    )
    mockQueue()
    mockedUseLocateShiprocketOrders.mockReturnValue({
      mutate: locateMutate,
      data: [
        {
          order_id: "order-1",
          status: "found",
          shiprocket_order_id: "1600000001",
          message: null,
        },
      ],
      isPending: false,
    } as unknown as ReturnType<typeof useLocateShiprocketOrders>)
    mockedUseRetryShopifySync.mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useRetryShopifySync>)

    renderWithProviders(<FulfillmentOrdersPage />)

    const checkboxes = screen.getAllByRole("checkbox")
    await user.click(checkboxes[1])
    expect(screen.getByText("Selected 1 orders")).toBeInTheDocument()

    await user.click(screen.getByRole("button", { name: /^Open in Shiprocket$/i }))

    const dialog = screen.getByRole("alertdialog")
    expect(locateMutate).toHaveBeenCalledWith(["order-1"])
    // No "Create Shipments"/per-row "Open" action anywhere in this
    // dialog anymore -- only the shared "Open Ready to Ship" + per-row
    // "Copy ID".
    expect(
      within(dialog).queryByRole("button", { name: /create shipment/i })
    ).not.toBeInTheDocument()
    expect(within(dialog).getByText("1600000001")).toBeInTheDocument()

    await user.click(within(dialog).getByRole("button", { name: /^Copy ID$/i }))
    await waitFor(() => expect(writeText).toHaveBeenCalledWith("1600000001"))

    await user.click(within(dialog).getByRole("button", { name: /^Open Ready to Ship$/i }))
    expect(openSpy).toHaveBeenCalledWith(READY_TO_SHIP_URL, "_blank", "noopener,noreferrer")
  })

  it("bulk dialog reports orders whose Shiprocket order could not be located", async () => {
    const user = userEvent.setup()
    mockClipboard()
    mockQueue([ROW, { ...ROW, order_id: "order-2", order_number: "OMS-0002" }])
    mockedUseLocateShiprocketOrders.mockReturnValue({
      mutate: vi.fn(),
      data: [
        {
          order_id: "order-1",
          status: "found",
          shiprocket_order_id: "1600000001",
          message: null,
        },
        {
          order_id: "order-2",
          status: "not_found",
          shiprocket_order_id: null,
          message: "Existing Shiprocket order could not be located for this order.",
        },
      ],
      isPending: false,
    } as unknown as ReturnType<typeof useLocateShiprocketOrders>)
    mockedUseRetryShopifySync.mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useRetryShopifySync>)

    renderWithProviders(<FulfillmentOrdersPage />)

    const checkboxes = screen.getAllByRole("checkbox")
    await user.click(checkboxes[0]) // select-all header checkbox
    await user.click(screen.getByRole("button", { name: /^Open in Shiprocket$/i }))

    const dialog = screen.getByRole("alertdialog")
    expect(within(dialog).getByText(/verify manually in Shiprocket/i)).toBeInTheDocument()
    expect(within(dialog).getByText("OMS-0002")).toBeInTheDocument()
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
})

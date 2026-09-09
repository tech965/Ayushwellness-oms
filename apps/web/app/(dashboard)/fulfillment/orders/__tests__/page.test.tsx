import { describe, expect, it, vi } from "vitest"
import { screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { renderWithProviders } from "@/test-utils/render-with-providers"
import FulfillmentOrdersPage from "@/app/(dashboard)/fulfillment/orders/page"
import {
  useBulkShipOrders,
  useShipmentQueue,
  useShipOrderFromQueue,
  useValidateBulkShip,
} from "@/services/shipment-queue"
import { useRetryShopifySync } from "@/services/shipments"
import { useTeamTelecallers } from "@/services/team"

const mockPush = vi.fn()

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
  useShipOrderFromQueue: vi.fn(),
  useBulkShipOrders: vi.fn(),
  useValidateBulkShip: vi.fn(),
}))

vi.mock("@/services/shipments", () => ({
  useRetryShopifySync: vi.fn(),
}))

vi.mock("@/services/team", () => ({
  useTeamTelecallers: vi.fn(),
}))

const mockedUseShipmentQueue = vi.mocked(useShipmentQueue)
const mockedUseTeamTelecallers = vi.mocked(useTeamTelecallers)
const mockedUseShipOrderFromQueue = vi.mocked(useShipOrderFromQueue)
const mockedUseBulkShipOrders = vi.mocked(useBulkShipOrders)
const mockedUseValidateBulkShip = vi.mocked(useValidateBulkShip)
const mockedUseRetryShopifySync = vi.mocked(useRetryShopifySync)

function mockShipHooks() {
  mockedUseShipOrderFromQueue.mockReturnValue({
    mutate: vi.fn(),
    isPending: false,
    variables: undefined,
  } as unknown as ReturnType<typeof useShipOrderFromQueue>)
  mockedUseBulkShipOrders.mockReturnValue({
    mutate: vi.fn(),
    isPending: false,
  } as unknown as ReturnType<typeof useBulkShipOrders>)
  mockedUseValidateBulkShip.mockReturnValue({
    mutate: vi.fn(),
    data: undefined,
    isPending: false,
  } as unknown as ReturnType<typeof useValidateBulkShip>)
  mockedUseRetryShopifySync.mockReturnValue({
    mutate: vi.fn(),
    isPending: false,
  } as unknown as ReturnType<typeof useRetryShopifySync>)
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
  it("lists orders needing shipment with a Ship Order row action", async () => {
    const user = userEvent.setup()
    const shipMutate = vi.fn()
    mockQueue()
    mockShipHooks()
    mockedUseShipOrderFromQueue.mockReturnValue({
      mutate: shipMutate,
      isPending: false,
      variables: undefined,
    } as unknown as ReturnType<typeof useShipOrderFromQueue>)

    renderWithProviders(<FulfillmentOrdersPage />)

    expect(screen.getByText("Orders Need Shipment")).toBeInTheDocument()
    expect(screen.getByText("Alice")).toBeInTheDocument()
    expect(screen.getByText("9990000001")).toBeInTheDocument()
    expect(screen.getByText("Sourabh")).toBeInTheDocument()
    expect(screen.getByText("Ready to Ship")).toBeInTheDocument()

    // No shipment yet -- the row offers to create one directly, no
    // navigation required.
    await user.click(screen.getByRole("button", { name: /^Ship Order$/i }))
    expect(shipMutate).toHaveBeenCalledWith("order-1", expect.anything())
    expect(mockPush).not.toHaveBeenCalled()
  })

  it("hides Ship Order and routes to the shipment processing page once a shipment already exists", async () => {
    const user = userEvent.setup()
    mockQueue([{ ...ROW, shipment_id: "ship-1", shipment_status: "pending" }])
    mockShipHooks()

    renderWithProviders(<FulfillmentOrdersPage />)

    expect(screen.queryByRole("button", { name: /^Ship Order$/i })).not.toBeInTheDocument()
    await user.click(screen.getByRole("button", { name: /^Process Shipment$/i }))
    expect(mockPush).toHaveBeenCalledWith("/shipments/ship-1")
  })

  it("selects orders, validates them, and ships only the ready ones", async () => {
    const user = userEvent.setup()
    const validateMutate = vi.fn()
    const bulkShipMutate = vi.fn((_ids, opts) => {
      opts.onSuccess({
        shipped_count: 1,
        failed_count: 0,
        results: [{ order_id: "order-1", success: true, message: null, shipment_id: "ship-9" }],
      })
    })
    mockQueue()
    mockShipHooks()
    mockedUseValidateBulkShip.mockReturnValue({
      mutate: validateMutate,
      data: [{ order_id: "order-1", ready: true, reason: null }],
      isPending: false,
    } as unknown as ReturnType<typeof useValidateBulkShip>)
    mockedUseBulkShipOrders.mockReturnValue({
      mutate: bulkShipMutate,
      isPending: false,
    } as unknown as ReturnType<typeof useBulkShipOrders>)

    renderWithProviders(<FulfillmentOrdersPage />)

    const checkboxes = screen.getAllByRole("checkbox")
    await user.click(checkboxes[1])

    expect(screen.getByText("Selected 1 orders")).toBeInTheDocument()
    await user.click(screen.getByRole("button", { name: /^Create Shipments$/i }))

    const dialog = screen.getByRole("alertdialog")
    expect(validateMutate).toHaveBeenCalledWith(["order-1"])
    await user.click(within(dialog).getByRole("button", { name: /^Create Shipments$/i }))

    expect(bulkShipMutate).toHaveBeenCalledWith(["order-1"], expect.anything())
  })

  it("excludes not-ready orders from the bulk ship call and shows the reason", async () => {
    const user = userEvent.setup()
    mockQueue([ROW, { ...ROW, order_id: "order-2", order_number: "OMS-0002" }])
    mockShipHooks()
    mockedUseValidateBulkShip.mockReturnValue({
      mutate: vi.fn(),
      data: [
        { order_id: "order-1", ready: true, reason: null },
        { order_id: "order-2", ready: false, reason: "Order has no shipping address on file." },
      ],
      isPending: false,
    } as unknown as ReturnType<typeof useValidateBulkShip>)
    const bulkShipMutate = vi.fn()
    mockedUseBulkShipOrders.mockReturnValue({
      mutate: bulkShipMutate,
      isPending: false,
    } as unknown as ReturnType<typeof useBulkShipOrders>)

    renderWithProviders(<FulfillmentOrdersPage />)

    const checkboxes = screen.getAllByRole("checkbox")
    await user.click(checkboxes[0]) // select-all header checkbox
    await user.click(screen.getByRole("button", { name: /^Create Shipments$/i }))

    const dialog = screen.getByRole("alertdialog")
    expect(within(dialog).getByText(/no shipping address on file/i)).toBeInTheDocument()
    const shipButton = within(dialog).getByRole("button", { name: /^Ship 1 Ready Order$/i })
    await user.click(shipButton)

    expect(bulkShipMutate).toHaveBeenCalledWith(["order-1"], expect.anything())
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

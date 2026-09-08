import { describe, expect, it, vi } from "vitest"
import { screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { renderWithProviders } from "@/test-utils/render-with-providers"
import FulfillmentOrdersPage from "@/app/(dashboard)/fulfillment/orders/page"
import {
  useBulkShipOrders,
  useShipmentQueue,
  useShipOrderFromQueue,
} from "@/services/shipment-queue"
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
}))

vi.mock("@/services/team", () => ({
  useTeamTelecallers: vi.fn(),
}))

const mockedUseShipmentQueue = vi.mocked(useShipmentQueue)
const mockedUseTeamTelecallers = vi.mocked(useTeamTelecallers)
const mockedUseShipOrderFromQueue = vi.mocked(useShipOrderFromQueue)
const mockedUseBulkShipOrders = vi.mocked(useBulkShipOrders)

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
  confirmed_at: "2026-09-01T00:00:00Z",
  confirmed_by_telecaller_id: "tc-1",
  confirmed_by_telecaller_name: "Sourabh",
  shipment_id: null,
  shipment_status: null,
  awb: null,
  courier_name: null,
}

describe("FulfillmentOrdersPage", () => {
  it("lists confirmed orders awaiting shipment with a Ship via Shiprocket row action", async () => {
    const user = userEvent.setup()
    const shipMutate = vi.fn()
    mockedUseShipmentQueue.mockReturnValue({
      isLoading: false,
      isError: false,
      error: null,
      data: {
        data: [ROW],
        meta: { page: 1, page_size: 20, total_items: 1, total_pages: 1 },
      },
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useShipmentQueue>)
    mockedUseTeamTelecallers.mockReturnValue({
      data: [{ telecaller_id: "tc-1", telecaller_name: "Sourabh" }],
    } as unknown as ReturnType<typeof useTeamTelecallers>)
    mockShipHooks()
    mockedUseShipOrderFromQueue.mockReturnValue({
      mutate: shipMutate,
      isPending: false,
      variables: undefined,
    } as unknown as ReturnType<typeof useShipOrderFromQueue>)

    renderWithProviders(<FulfillmentOrdersPage />)

    expect(screen.getByText("Confirmed Orders — Shipment Queue")).toBeInTheDocument()
    expect(screen.getByText("Alice")).toBeInTheDocument()
    expect(screen.getByText("9990000001")).toBeInTheDocument()
    expect(screen.getByText("Sourabh")).toBeInTheDocument()
    expect(screen.getByText("Not created")).toBeInTheDocument()

    // No shipment yet -- the row offers to create one directly, no
    // navigation required.
    await user.click(screen.getByRole("button", { name: /Ship via Shiprocket/i }))
    expect(shipMutate).toHaveBeenCalledWith("order-1", expect.anything())
    expect(mockPush).not.toHaveBeenCalled()
  })

  it("routes to the shipment processing page once a shipment already exists", async () => {
    const user = userEvent.setup()
    mockedUseShipmentQueue.mockReturnValue({
      isLoading: false,
      isError: false,
      error: null,
      data: {
        data: [{ ...ROW, shipment_id: "ship-1", shipment_status: "pending" }],
        meta: { page: 1, page_size: 20, total_items: 1, total_pages: 1 },
      },
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useShipmentQueue>)
    mockedUseTeamTelecallers.mockReturnValue({
      data: [{ telecaller_id: "tc-1", telecaller_name: "Sourabh" }],
    } as unknown as ReturnType<typeof useTeamTelecallers>)
    mockShipHooks()

    renderWithProviders(<FulfillmentOrdersPage />)

    await user.click(screen.getByRole("button", { name: /^Process Shipment$/i }))
    expect(mockPush).toHaveBeenCalledWith("/shipments/ship-1")
  })

  it("selects orders and bulk ships them via Shiprocket, reporting per-order results", async () => {
    const user = userEvent.setup()
    const bulkShipMutate = vi.fn((_ids, opts) => {
      opts.onSuccess({
        shipped_count: 1,
        failed_count: 0,
        results: [{ order_id: "order-1", success: true, message: null, shipment_id: "ship-9" }],
      })
    })
    mockedUseShipmentQueue.mockReturnValue({
      isLoading: false,
      isError: false,
      error: null,
      data: {
        data: [ROW],
        meta: { page: 1, page_size: 20, total_items: 1, total_pages: 1 },
      },
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useShipmentQueue>)
    mockedUseTeamTelecallers.mockReturnValue({
      data: [{ telecaller_id: "tc-1", telecaller_name: "Sourabh" }],
    } as unknown as ReturnType<typeof useTeamTelecallers>)
    mockShipHooks()
    mockedUseBulkShipOrders.mockReturnValue({
      mutate: bulkShipMutate,
      isPending: false,
    } as unknown as ReturnType<typeof useBulkShipOrders>)

    renderWithProviders(<FulfillmentOrdersPage />)

    const checkboxes = screen.getAllByRole("checkbox")
    await user.click(checkboxes[1])

    expect(screen.getByText("1 orders selected")).toBeInTheDocument()
    await user.click(screen.getByRole("button", { name: /Bulk Ship via Shiprocket \(1\)/i }))

    const dialog = screen.getByRole("alertdialog")
    expect(
      within(dialog).getByText("Ship 1 selected orders via Shiprocket?")
    ).toBeInTheDocument()
    await user.click(within(dialog).getByRole("button", { name: /^Ship Orders$/i }))

    expect(bulkShipMutate).toHaveBeenCalledWith(["order-1"], expect.anything())
  })

  it("does not show the bulk ship button when nothing is selected", () => {
    mockedUseShipmentQueue.mockReturnValue({
      isLoading: false,
      isError: false,
      error: null,
      data: {
        data: [ROW],
        meta: { page: 1, page_size: 20, total_items: 1, total_pages: 1 },
      },
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useShipmentQueue>)
    mockedUseTeamTelecallers.mockReturnValue({
      data: [{ telecaller_id: "tc-1", telecaller_name: "Sourabh" }],
    } as unknown as ReturnType<typeof useTeamTelecallers>)
    mockShipHooks()

    renderWithProviders(<FulfillmentOrdersPage />)

    expect(
      screen.queryByRole("button", { name: /Bulk Ship via Shiprocket/i })
    ).not.toBeInTheDocument()
  })

  it("shows an empty state when nothing is awaiting shipment", () => {
    mockedUseShipmentQueue.mockReturnValue({
      isLoading: false,
      isError: false,
      error: null,
      data: { data: [], meta: { page: 1, page_size: 20, total_items: 0, total_pages: 0 } },
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useShipmentQueue>)
    mockedUseTeamTelecallers.mockReturnValue({
      data: [],
    } as unknown as ReturnType<typeof useTeamTelecallers>)
    mockShipHooks()

    renderWithProviders(<FulfillmentOrdersPage />)

    expect(screen.getByText("No confirmed orders awaiting shipment")).toBeInTheDocument()
  })
})

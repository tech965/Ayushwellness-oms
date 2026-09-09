import { describe, expect, it, vi } from "vitest"
import { screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { renderWithProviders } from "@/test-utils/render-with-providers"
import FulfillmentConfirmedOrdersPage from "@/app/(dashboard)/fulfillment/confirmed-orders/page"
import { useTelecallerConfirmedOrders } from "@/services/orders"
import {
  useBulkShipOrders,
  useShipOrderFromQueue,
  useValidateBulkShip,
} from "@/services/shipment-queue"
import { useRetryShopifySync } from "@/services/shipments"
import { useTeamTelecallers } from "@/services/team"

const mockPush = vi.fn()

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
}))

vi.mock("@/services/shipment-queue", () => ({
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

const mockedUseTelecallerConfirmedOrders = vi.mocked(useTelecallerConfirmedOrders)
const mockedUseTeamTelecallers = vi.mocked(useTeamTelecallers)
const mockedUseShipOrderFromQueue = vi.mocked(useShipOrderFromQueue)
const mockedUseBulkShipOrders = vi.mocked(useBulkShipOrders)
const mockedUseValidateBulkShip = vi.mocked(useValidateBulkShip)
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

describe("FulfillmentConfirmedOrdersPage", () => {
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

  it("shows Ship Order directly in the row for an order eligible for shipment", async () => {
    const user = userEvent.setup()
    const shipMutate = vi.fn()
    mockQuery()
    mockShipHooks()
    mockedUseShipOrderFromQueue.mockReturnValue({
      mutate: shipMutate,
      isPending: false,
      variables: undefined,
    } as unknown as ReturnType<typeof useShipOrderFromQueue>)

    renderWithProviders(<FulfillmentConfirmedOrdersPage />)

    await user.click(screen.getByRole("button", { name: /^Ship Order$/i }))
    expect(shipMutate).toHaveBeenCalledWith("order-1", expect.anything())
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

  it("selects orders via checkbox and runs the bulk-ship validate-then-ship flow", async () => {
    const user = userEvent.setup()
    const validateMutate = vi.fn()
    const bulkShipMutate = vi.fn((_ids, opts) => {
      opts.onSuccess({
        shipped_count: 1,
        failed_count: 0,
        results: [{ order_id: "order-1", success: true, message: null, shipment_id: "ship-9" }],
      })
    })
    mockQuery()
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

    renderWithProviders(<FulfillmentConfirmedOrdersPage />)

    const checkboxes = screen.getAllByRole("checkbox")
    await user.click(checkboxes[1])
    expect(screen.getByText("Selected 1 orders")).toBeInTheDocument()

    await user.click(screen.getByRole("button", { name: /^Create Shipments$/i }))
    expect(validateMutate).toHaveBeenCalledWith(["order-1"])

    const dialog = screen.getByRole("alertdialog")
    await user.click(within(dialog).getByRole("button", { name: /^Create Shipments$/i }))
    expect(bulkShipMutate).toHaveBeenCalledWith(["order-1"], expect.anything())
  })

  it("shows an empty state when nothing has been confirmed by Telecalling yet", () => {
    mockQuery([])
    mockShipHooks()
    renderWithProviders(<FulfillmentConfirmedOrdersPage />)
    expect(screen.getByText("No Telecaller-confirmed orders yet")).toBeInTheDocument()
  })
})

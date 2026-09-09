import { describe, expect, it, vi } from "vitest"
import { screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { renderWithProviders } from "@/test-utils/render-with-providers"
import ShipmentQueuePage from "@/app/(dashboard)/shipment-queue/page"
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
  usePathname: () => "/shipment-queue",
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

const ROW = {
  order_id: "order-1",
  order_number: "OMS-0001",
  customer_name: "Alice",
  customer_phone: "9990000001",
  item_summary: "Ashwagandha",
  sku_summary: "ASH-001",
  total_quantity: 1,
  total_amount: "499.00",
  payment_type: "cod",
  payment_status: "pending",
  confirmed_at: "2026-09-01T00:00:00Z",
  confirmed_by_telecaller_id: "tc-1",
  confirmed_by_telecaller_name: "Sourabh",
  shipment_id: null,
  shipment_status: null,
  shopify_sync_status: null,
  awb: null,
  courier_name: null,
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

describe("ShipmentQueuePage (legacy alias for /fulfillment/orders)", () => {
  it("renders orders needing shipment with a Ship Order action", async () => {
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

    renderWithProviders(<ShipmentQueuePage />)

    expect(screen.getByText("Alice")).toBeInTheDocument()
    expect(screen.getByText("OMS-0001")).toBeInTheDocument()
    expect(screen.getByText("Ready to Ship")).toBeInTheDocument()

    await user.click(screen.getByRole("button", { name: /^Ship Order$/i }))
    expect(shipMutate).toHaveBeenCalledWith("order-1", expect.anything())
  })

  it("shows an empty state when there are no confirmed orders awaiting shipment", () => {
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

    renderWithProviders(<ShipmentQueuePage />)

    expect(screen.getByText("No confirmed orders awaiting shipment")).toBeInTheDocument()
  })
})

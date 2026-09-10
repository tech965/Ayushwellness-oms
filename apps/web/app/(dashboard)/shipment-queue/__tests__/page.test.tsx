import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { renderWithProviders } from "@/test-utils/render-with-providers"
import ShipmentQueuePage from "@/app/(dashboard)/shipment-queue/page"
import { toast } from "sonner"
import { useLocateShiprocketOrders, useShipmentQueue } from "@/services/shipment-queue"
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
  shiprocket_order_url: "https://app.shiprocket.in/seller/orders/readytoship?order_ids=1576398335",
  awb: null,
  courier_name: null,
}

function mockShipHooks() {
  mockedUseLocateShiprocketOrders.mockReturnValue({
    mutate: vi.fn((ids: string[], opts?: { onSuccess?: (r: unknown) => void }) =>
      opts?.onSuccess?.(
        ids.map((id) => ({
          order_id: id,
          status: "not_found",
          shiprocket_order_url: null,
          message: null,
        }))
      )
    ),
    data: undefined,
    isPending: false,
  } as unknown as ReturnType<typeof useLocateShiprocketOrders>)
  mockedUseRetryShopifySync.mockReturnValue({
    mutate: vi.fn(),
    isPending: false,
  } as unknown as ReturnType<typeof useRetryShopifySync>)
}

describe("ShipmentQueuePage (legacy alias for /fulfillment/orders)", () => {
  let openSpy: ReturnType<typeof vi.spyOn>

  beforeEach(() => {
    openSpy = vi.spyOn(window, "open").mockReturnValue(null)
  })

  afterEach(() => {
    openSpy.mockRestore()
    vi.clearAllMocks()
  })

  it("renders orders needing shipment with a Ship Order action that opens the real Shiprocket order page", async () => {
    const user = userEvent.setup()
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

    renderWithProviders(<ShipmentQueuePage />)

    expect(screen.getByText("Alice")).toBeInTheDocument()
    expect(screen.getByText("OMS-0001")).toBeInTheDocument()
    expect(screen.getByText("Ready to Ship")).toBeInTheDocument()

    await user.click(screen.getByRole("button", { name: /^Ship Order$/i }))
    expect(openSpy).toHaveBeenCalledWith(
      "https://app.shiprocket.in/seller/orders/readytoship?order_ids=1576398335",
      "_blank",
      "noopener,noreferrer"
    )
  })

  it("shows the unavailable message instead of calling any Shiprocket API when no order id is stored", async () => {
    const user = userEvent.setup()
    mockedUseShipmentQueue.mockReturnValue({
      isLoading: false,
      isError: false,
      error: null,
      data: {
        data: [{ ...ROW, shiprocket_order_url: null }],
        meta: { page: 1, page_size: 20, total_items: 1, total_pages: 1 },
      },
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useShipmentQueue>)
    mockedUseTeamTelecallers.mockReturnValue({
      data: [{ telecaller_id: "tc-1", telecaller_name: "Sourabh" }],
    } as unknown as ReturnType<typeof useTeamTelecallers>)
    mockShipHooks()

    renderWithProviders(<ShipmentQueuePage />)

    await user.click(screen.getByRole("button", { name: /^Ship Order$/i }))
    expect(openSpy).not.toHaveBeenCalled()
    expect(toast.error).toHaveBeenCalledWith(
      "Shiprocket order link is unavailable for this order.",
      expect.anything()
    )
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

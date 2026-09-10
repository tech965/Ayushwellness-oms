import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { renderWithProviders } from "@/test-utils/render-with-providers"
import ShipmentStaffOrdersPage from "@/app/(dashboard)/shipment-staff/orders/page"
import { toast } from "sonner"
import {
  useLocateShiprocketOrdersForMyScope,
  useMyConfirmedOrders,
} from "@/services/shipment-staff"

const mockPush = vi.fn()

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
  useLocateShiprocketOrdersForMyScope: vi.fn(),
}))

const mockedUseMyConfirmedOrders = vi.mocked(useMyConfirmedOrders)
const mockedUseLocateShiprocketOrdersForMyScope = vi.mocked(useLocateShiprocketOrdersForMyScope)

/** Default: a scoped locate that reports "not found" -- individual
 * tests override `mutate` for the found path.
 */
function mockLocate(
  impl: (ids: string[], opts?: { onSuccess?: (r: unknown) => void }) => void = (ids, opts) =>
    opts?.onSuccess?.(
      ids.map((id) => ({
        order_id: id,
        status: "not_found",
        shiprocket_order_url: null,
        message: null,
      }))
    )
) {
  mockedUseLocateShiprocketOrdersForMyScope.mockReturnValue({
    mutate: vi.fn(impl),
    isPending: false,
  } as unknown as ReturnType<typeof useLocateShiprocketOrdersForMyScope>)
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
  shiprocket_order_url: "https://app.shiprocket.in/seller/orders/readytoship?order_ids=1576398335",
  awb: null,
  courier_name: null,
}

describe("ShipmentStaffOrdersPage", () => {
  let openSpy: ReturnType<typeof vi.spyOn>

  beforeEach(() => {
    openSpy = vi.spyOn(window, "open").mockReturnValue(null)
    mockLocate()
  })

  afterEach(() => {
    openSpy.mockRestore()
    vi.clearAllMocks()
  })

  it("lists only this Shipment Staff user's scoped confirmed orders, with no telecaller filter", async () => {
    const user = userEvent.setup()
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

    // "Ship Order" opens the real Shiprocket order page in a new tab --
    // never a Shiprocket create-shipment API call from here (same rule
    // as the Fulfillment/Admin queue's `ShipmentActionCell`).
    await user.click(screen.getByRole("button", { name: /^Ship Order$/i }))
    expect(openSpy).toHaveBeenCalledWith(
      "https://app.shiprocket.in/seller/orders/readytoship?order_ids=1576398335",
      "_blank",
      "noopener,noreferrer"
    )
  })

  it("shows the unavailable message instead of calling any Shiprocket API when no order id is stored", async () => {
    const user = userEvent.setup()
    mockedUseMyConfirmedOrders.mockReturnValue({
      isLoading: false,
      isError: false,
      error: null,
      data: {
        data: [{ ...ROW, shiprocket_order_url: null }],
        meta: { page: 1, page_size: 20, total_items: 1, total_pages: 1 },
      },
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useMyConfirmedOrders>)

    renderWithProviders(<ShipmentStaffOrdersPage />)

    await user.click(screen.getByRole("button", { name: /^Ship Order$/i }))
    expect(openSpy).not.toHaveBeenCalled()
    expect(toast.error).toHaveBeenCalledWith(
      "Shiprocket order link is unavailable for this order.",
      expect.anything()
    )
  })

  it("opens the real Shiprocket order page when the scoped live locate resolves the id", async () => {
    const user = userEvent.setup()
    mockLocate((ids, opts) =>
      opts?.onSuccess?.(
        ids.map((id) => ({
          order_id: id,
          status: "found",
          shiprocket_order_url: "https://app.shiprocket.in/seller/orders/readytoship?order_ids=1576398335",
          message: null,
        }))
      )
    )
    mockedUseMyConfirmedOrders.mockReturnValue({
      isLoading: false,
      isError: false,
      error: null,
      data: {
        data: [{ ...ROW, shiprocket_order_url: null }],
        meta: { page: 1, page_size: 20, total_items: 1, total_pages: 1 },
      },
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useMyConfirmedOrders>)

    renderWithProviders(<ShipmentStaffOrdersPage />)

    await user.click(screen.getByRole("button", { name: /^Ship Order$/i }))
    expect(openSpy).toHaveBeenCalledWith(
      "https://app.shiprocket.in/seller/orders/readytoship?order_ids=1576398335",
      "_blank",
      "noopener,noreferrer"
    )
  })

  it("opens the real Shiprocket order page for Process Shipment once a shipment already exists", async () => {
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
    expect(openSpy).toHaveBeenCalledWith(
      "https://app.shiprocket.in/seller/orders/readytoship?order_ids=1576398335",
      "_blank",
      "noopener,noreferrer"
    )
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

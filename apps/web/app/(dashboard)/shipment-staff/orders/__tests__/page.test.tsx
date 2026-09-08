import { describe, expect, it, vi } from "vitest"
import { screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { renderWithProviders } from "@/test-utils/render-with-providers"
import ShipmentStaffOrdersPage from "@/app/(dashboard)/shipment-staff/orders/page"
import { useMyConfirmedOrders, useShipMyConfirmedOrder } from "@/services/shipment-staff"

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
  useShipMyConfirmedOrder: vi.fn(),
}))

const mockedUseMyConfirmedOrders = vi.mocked(useMyConfirmedOrders)
const mockedUseShipMyConfirmedOrder = vi.mocked(useShipMyConfirmedOrder)

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
  awb: null,
  courier_name: null,
}

describe("ShipmentStaffOrdersPage", () => {
  it("lists only this Shipment Staff user's scoped confirmed orders, with no telecaller filter", async () => {
    const user = userEvent.setup()
    const shipMutate = vi.fn()
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
    mockedUseShipMyConfirmedOrder.mockReturnValue({
      mutate: shipMutate,
      isPending: false,
      variables: undefined,
    } as unknown as ReturnType<typeof useShipMyConfirmedOrder>)

    renderWithProviders(<ShipmentStaffOrdersPage />)

    expect(screen.getByText("Confirmed Orders")).toBeInTheDocument()
    expect(screen.getByText("Alice")).toBeInTheDocument()
    expect(screen.getByText("Sourabh")).toBeInTheDocument()
    // Unlike the Fulfillment/Admin queue, there is no telecaller filter --
    // every row already belongs to this Shipment Staff user's own scope.
    expect(screen.queryByText("All telecallers")).not.toBeInTheDocument()

    await user.click(screen.getByRole("button", { name: /Ship via Shiprocket/i }))
    expect(shipMutate).toHaveBeenCalledWith("order-1", expect.anything())
  })

  it("shows an empty state scoped to this user's own Telecallers", () => {
    mockedUseMyConfirmedOrders.mockReturnValue({
      isLoading: false,
      isError: false,
      error: null,
      data: { data: [], meta: { page: 1, page_size: 20, total_items: 0, total_pages: 0 } },
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useMyConfirmedOrders>)
    mockedUseShipMyConfirmedOrder.mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
      variables: undefined,
    } as unknown as ReturnType<typeof useShipMyConfirmedOrder>)

    renderWithProviders(<ShipmentStaffOrdersPage />)

    expect(screen.getByText("No confirmed orders awaiting shipment")).toBeInTheDocument()
  })
})

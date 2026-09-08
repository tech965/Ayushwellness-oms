import { describe, expect, it, vi } from "vitest"
import { screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { renderWithProviders } from "@/test-utils/render-with-providers"
import FulfillmentOrdersPage from "@/app/(dashboard)/fulfillment/orders/page"
import { useShipmentQueue } from "@/services/shipment-queue"
import { useTeamTelecallers } from "@/services/team"

const mockPush = vi.fn()

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockPush, replace: vi.fn(), back: vi.fn() }),
  usePathname: () => "/fulfillment/orders",
  useSearchParams: () => new URLSearchParams(),
}))

vi.mock("@/services/shipment-queue", () => ({
  useShipmentQueue: vi.fn(),
}))

vi.mock("@/services/team", () => ({
  useTeamTelecallers: vi.fn(),
}))

const mockedUseShipmentQueue = vi.mocked(useShipmentQueue)
const mockedUseTeamTelecallers = vi.mocked(useTeamTelecallers)

const ROW = {
  order_id: "order-1",
  order_number: "OMS-0001",
  customer_name: "Alice",
  customer_phone: "9990000001",
  item_summary: "Ashwagandha x2",
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
  it("lists confirmed orders awaiting shipment with a Process Shipment action", async () => {
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

    renderWithProviders(<FulfillmentOrdersPage />)

    expect(screen.getByText("Confirmed Orders — Shipment Queue")).toBeInTheDocument()
    expect(screen.getByText("Alice")).toBeInTheDocument()
    expect(screen.getByText("9990000001")).toBeInTheDocument()
    expect(screen.getByText("Sourabh")).toBeInTheDocument()
    expect(screen.getByText("Not created")).toBeInTheDocument()

    await user.click(screen.getByRole("button", { name: /Process Shipment/i }))
    expect(mockPush).toHaveBeenCalledWith("/orders/order-1")
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

    renderWithProviders(<FulfillmentOrdersPage />)

    expect(screen.getByText("No confirmed orders awaiting shipment")).toBeInTheDocument()
  })
})

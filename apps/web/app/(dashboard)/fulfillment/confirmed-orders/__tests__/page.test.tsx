import { describe, expect, it, vi } from "vitest"
import { screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { renderWithProviders } from "@/test-utils/render-with-providers"
import FulfillmentConfirmedOrdersPage from "@/app/(dashboard)/fulfillment/confirmed-orders/page"
import { useTelecallerConfirmedOrders } from "@/services/orders"
import { useTeamTelecallers } from "@/services/team"

const mockPush = vi.fn()

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockPush, replace: vi.fn(), back: vi.fn() }),
  usePathname: () => "/fulfillment/confirmed-orders",
  useSearchParams: () => new URLSearchParams(),
}))

vi.mock("@/services/orders", () => ({
  useTelecallerConfirmedOrders: vi.fn(),
}))

vi.mock("@/services/team", () => ({
  useTeamTelecallers: vi.fn(),
}))

const mockedUseTelecallerConfirmedOrders = vi.mocked(useTelecallerConfirmedOrders)
const mockedUseTeamTelecallers = vi.mocked(useTeamTelecallers)

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

describe("FulfillmentConfirmedOrdersPage", () => {
  it("shows the page title and subtitle", () => {
    mockQuery()
    renderWithProviders(<FulfillmentConfirmedOrdersPage />)
    expect(screen.getByText("Confirmed by Telecaller")).toBeInTheDocument()
    expect(screen.getByText("1 orders confirmed by Telecalling.")).toBeInTheDocument()
  })

  it("always shows the real Telecaller name, never a dash, when confirmed_by_telecaller_id is set", () => {
    mockQuery()
    renderWithProviders(<FulfillmentConfirmedOrdersPage />)
    expect(screen.getByText("Rahul Sharma")).toBeInTheDocument()
  })

  it("renders order/customer/items/amount/status columns from the example row", () => {
    mockQuery()
    renderWithProviders(<FulfillmentConfirmedOrdersPage />)
    expect(screen.getByText("AWL95408")).toBeInTheDocument()
    expect(screen.getByText("Ranjit")).toBeInTheDocument()
    expect(screen.getByText("Ayush Wellness Herbal Masala x1")).toBeInTheDocument()
  })

  it("navigates to the order detail page from the View action, without filtering by shipment status", async () => {
    const user = userEvent.setup()
    mockQuery()
    renderWithProviders(<FulfillmentConfirmedOrdersPage />)
    await user.click(screen.getByRole("button", { name: /^View$/i }))
    expect(mockPush).toHaveBeenCalledWith("/orders/order-1")
  })

  it("shows a shipped order (not just orders still needing shipment) -- this is a history view", () => {
    mockQuery([{ ...ROW, status: "shipped", fulfillment_status: "fulfilled", shipment_status: "delivered" }])
    renderWithProviders(<FulfillmentConfirmedOrdersPage />)
    expect(screen.getByText("AWL95408")).toBeInTheDocument()
  })

  it("shows an empty state when nothing has been confirmed by Telecalling yet", () => {
    mockQuery([])
    renderWithProviders(<FulfillmentConfirmedOrdersPage />)
    expect(screen.getByText("No Telecaller-confirmed orders yet")).toBeInTheDocument()
  })
})

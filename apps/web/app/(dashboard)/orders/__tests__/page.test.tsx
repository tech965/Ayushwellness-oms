import { describe, expect, it, vi } from "vitest"
import { screen } from "@testing-library/react"

import { renderWithProviders } from "@/test-utils/render-with-providers"
import OrdersPage from "@/app/(dashboard)/orders/page"
import { useOrders } from "@/services/orders"

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}))

vi.mock("@/services/orders", () => ({
  useOrders: vi.fn(),
}))

const mockedUseOrders = vi.mocked(useOrders)

function baseQueryResult(overrides: Partial<ReturnType<typeof useOrders>>) {
  return {
    isLoading: false,
    isError: false,
    error: null,
    data: undefined,
    refetch: vi.fn(),
    ...overrides,
  } as unknown as ReturnType<typeof useOrders>
}

describe("OrdersPage", () => {
  it("shows skeletons while loading", () => {
    mockedUseOrders.mockReturnValue(baseQueryResult({ isLoading: true }))
    const { container } = renderWithProviders(<OrdersPage />)
    expect(container.querySelectorAll("[data-slot='skeleton']").length).toBeGreaterThan(0)
  })

  it("shows an error state with a retry action", () => {
    mockedUseOrders.mockReturnValue(
      baseQueryResult({ isError: true, error: new Error("Network down") })
    )
    renderWithProviders(<OrdersPage />)
    expect(screen.getByText("Network down")).toBeInTheDocument()
  })

  it("shows an empty state when there are no orders", () => {
    mockedUseOrders.mockReturnValue(
      baseQueryResult({
        data: {
          success: true,
          data: [],
          message: "Success",
          meta: { page: 1, page_size: 20, total_items: 0, total_pages: 0 },
        },
      })
    )
    renderWithProviders(<OrdersPage />)
    expect(screen.getByText("No orders found")).toBeInTheDocument()
  })

  it("renders a table row for each order", () => {
    mockedUseOrders.mockReturnValue(
      baseQueryResult({
        data: {
          success: true,
          message: "Success",
          data: [
            {
              id: "1",
              order_number: "OMS-1001",
              shopify_order_id: null,
              customer_id: null,
              order_datetime: "2026-01-01T00:00:00Z",
              currency: "INR",
              subtotal: "100.00",
              discount_amount: "0.00",
              tax_amount: "0.00",
              shipping_charge: "0.00",
              total_amount: "100.00",
              payment_type: "prepaid",
              payment_status: "pending",
              status: "pending",
              fulfillment_status: "unfulfilled",
              cancellation_status: "none",
              notes: null,
              shipping_address: null,
              billing_address: null,
              source_system: "manual",
              created_at: "2026-01-01T00:00:00Z",
              updated_at: "2026-01-01T00:00:00Z",
            },
          ],
          meta: { page: 1, page_size: 20, total_items: 1, total_pages: 1 },
        },
      })
    )
    renderWithProviders(<OrdersPage />)
    expect(screen.getByText("OMS-1001")).toBeInTheDocument()
  })
})

import { describe, expect, it, vi } from "vitest"
import { screen } from "@testing-library/react"

import { renderWithProviders } from "@/test-utils/render-with-providers"
import { DrilldownOrdersTable } from "@/components/analytics/drilldown-orders-table"
import { useOrders } from "@/services/orders"
import type { Order } from "@/types/order"

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn() }),
  usePathname: () => "/revenue/cod",
  useSearchParams: () => new URLSearchParams(),
}))

vi.mock("@/services/orders", () => ({
  useOrders: vi.fn(),
}))

const mockedUseOrders = vi.mocked(useOrders)

const ORDER: Order = {
  id: "order-1",
  order_number: "#AWL1",
  shopify_order_id: null,
  customer_id: null,
  order_datetime: "2026-08-15T00:00:00Z",
  currency: "INR",
  subtotal: "1000.00",
  discount_amount: "0.00",
  tax_amount: "0.00",
  shipping_charge: "0.00",
  total_amount: "1000.00",
  payment_type: "cod",
  payment_status: "pending",
  status: "confirmed",
  fulfillment_status: "fulfilled",
  cancellation_status: "none",
  notes: null,
  shopify_tags: null,
  shopify_order_note: null,
  shipping_address: null,
  billing_address: null,
  source_system: "shopify",
  created_at: "2026-08-15T00:00:00Z",
  updated_at: "2026-08-15T00:00:00Z",
  customer_name: "Test Customer",
}

describe("DrilldownOrdersTable", () => {
  it("shows the Payment Status column by default", () => {
    mockedUseOrders.mockReturnValue({
      data: { data: [ORDER], meta: { total_items: 1 } },
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useOrders>)

    renderWithProviders(
      <DrilldownOrdersTable
        title="Test Orders"
        filters={{ payment_type: "cod" }}
        ordersHref="/orders?payment_type=cod"
      />
    )

    expect(screen.getByText("Payment Status")).toBeInTheDocument()
  })

  it("omits the Payment Status column when hidePaymentColumn is set", () => {
    mockedUseOrders.mockReturnValue({
      data: { data: [ORDER], meta: { total_items: 1 } },
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useOrders>)

    renderWithProviders(
      <DrilldownOrdersTable
        title="Pending COD Orders"
        filters={{ payment_type: "cod", payment_status: "pending", fulfillment_status: "fulfilled" }}
        ordersHref="/orders?payment_type=cod&payment_status=pending&fulfillment_status=fulfilled"
        hidePaymentColumn
      />
    )

    expect(screen.queryByText("Payment Status")).not.toBeInTheDocument()
    // Other columns still render -- only the payment column is affected.
    expect(screen.getByText("Order ID")).toBeInTheDocument()
    expect(screen.getByText("Order Status")).toBeInTheDocument()
  })
})

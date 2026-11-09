import { beforeEach, describe, expect, it, vi } from "vitest"
import { screen } from "@testing-library/react"

import { renderWithProviders } from "@/test-utils/render-with-providers"
import { LiveDashboard } from "@/components/dashboard/live-dashboard"
import { useLiveAnalyticsSummary } from "@/services/live"
import type { LiveAnalyticsSummary } from "@/types/live"

vi.mock("@/services/live", () => ({
  useLiveAnalyticsSummary: vi.fn(),
  useLiveMode: vi.fn(),
}))

function summary(overrides: Partial<LiveAnalyticsSummary> = {}): LiveAnalyticsSummary {
  return {
    complete: true,
    orders_scanned: 120,
    warnings: [],
    total_orders: 120,
    cancelled_orders: 4,
    revenue: "59880.00",
    average_order_value: "516.21",
    by_fulfillment_status: [{ label: "unfulfilled", count: 90, amount: "44910.00" }],
    by_payment_type: [{ label: "cod", count: 70, amount: "34930.00" }],
    by_payment_status: [{ label: "paid", count: 50, amount: "24950.00" }],
    by_shopify_delivery_status: [{ label: "not_shipped", count: 120, amount: "59880.00" }],
    daily: [],
    top_products: [{ sku: "S1", product_name: "Masala Pack", quantity: 30, revenue: "6000.00" }],
    confirmed_orders: 10,
    assigned_orders: 40,
    unassigned_orders: 80,
    unavailable: ["Cashfree payment, refund and settlement detail (not implemented yet)."],
    ...overrides,
  }
}

function mockQuery(value: Partial<ReturnType<typeof useLiveAnalyticsSummary>>) {
  vi.mocked(useLiveAnalyticsSummary).mockReturnValue({
    data: undefined,
    isLoading: false,
    isError: false,
    error: null,
    ...value,
  } as ReturnType<typeof useLiveAnalyticsSummary>)
}

describe("LiveDashboard", () => {
  beforeEach(() => vi.clearAllMocks())

  it("shows live figures and what is genuinely unavailable", () => {
    mockQuery({ data: summary() })
    renderWithProviders(<LiveDashboard />)

    expect(screen.getByTestId("live-dashboard")).toBeInTheDocument()
    expect(screen.getByText("Masala Pack")).toBeInTheDocument()
    expect(screen.getByText(/Based on 120 orders\./)).toBeInTheDocument()
    expect(screen.getByTestId("live-unavailable")).toHaveTextContent(/Cashfree/)
    expect(screen.queryByTestId("live-result-warning")).toBeNull()
  })

  it("says so when the scan was partial instead of presenting totals as global", () => {
    mockQuery({
      data: summary({
        complete: false,
        warnings: ["Stopped after 20 Shopify pages; results are incomplete."],
      }),
    })
    renderWithProviders(<LiveDashboard />)

    expect(screen.getByTestId("live-result-warning")).toBeInTheDocument()
    expect(screen.getByText(/partial scan — not a global total/)).toBeInTheDocument()
  })

  it("shows a readable error, not a raw API payload", () => {
    mockQuery({ isError: true, error: new Error("Shopify is temporarily unavailable.") })
    renderWithProviders(<LiveDashboard />)

    expect(screen.getByText(/Could not load live figures/)).toBeInTheDocument()
    expect(screen.getByText(/Shopify is temporarily unavailable/)).toBeInTheDocument()
  })
})

import { describe, expect, it, vi } from "vitest"
import { screen } from "@testing-library/react"

import { renderWithProviders } from "@/test-utils/render-with-providers"
import OrderBreakdownPage from "@/app/(dashboard)/orders/breakdown/page"
import { useAnalyticsSummary, useBreakdowns, useRevenueTimeseries } from "@/services/analytics"
import { useOrders } from "@/services/orders"
import type { AnalyticsSummary, Breakdowns, RevenueTimeseries } from "@/types/analytics"

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn() }),
  usePathname: () => "/orders/breakdown",
  useSearchParams: () => new URLSearchParams(),
}))

vi.mock("@/services/analytics", () => ({
  useAnalyticsSummary: vi.fn(),
  useBreakdowns: vi.fn(),
  useRevenueTimeseries: vi.fn(),
}))

vi.mock("@/services/orders", () => ({
  useOrders: vi.fn(),
}))

const mockedUseSummary = vi.mocked(useAnalyticsSummary)
const mockedUseBreakdowns = vi.mocked(useBreakdowns)
const mockedUseRevenueTimeseries = vi.mocked(useRevenueTimeseries)
const mockedUseOrders = vi.mocked(useOrders)

const EMPTY_TIMESERIES: RevenueTimeseries = { interval: "day", points: [] }

function kpi(current: string) {
  return { current, previous: "0", change_pct: null }
}

const SUMMARY: AnalyticsSummary = {
  date_from: "2026-08-01T00:00:00Z",
  date_to: "2026-08-29T00:00:00Z",
  total_orders: kpi("100"),
  total_revenue: kpi("500000.00"),
  total_customers: kpi("40"),
  total_products: kpi("10"),
  fulfilled_orders: kpi("60"),
  unfulfilled_orders: kpi("40"),
  cod_orders: kpi("70"),
  prepaid_orders: kpi("30"),
  pending_orders: kpi("25"),
  cod_value: kpi("350000.00"),
  prepaid_value: kpi("150000.00"),
  delivered_shipments: kpi("50"),
  in_transit_shipments: kpi("5"),
  out_for_delivery_shipments: kpi("2"),
  delayed_shipments: kpi("1"),
  open_ndr: kpi("3"),
  open_rto: kpi("1"),
  returns: kpi("2"),
  refunds: kpi("1"),
}

const BREAKDOWNS: Breakdowns = {
  order_status: [
    { status: "pending", count: 25 },
    { status: "confirmed", count: 60 },
    { status: "cancelled", count: 5 },
  ],
  payment_type: [],
  payment_status: [],
  fulfillment_status: [],
  shipment_status: [],
}

describe("OrderBreakdownPage", () => {
  it("renders counts, percentages, and values, each linking to the right filtered Orders view", () => {
    mockedUseSummary.mockReturnValue({
      data: SUMMARY,
      isLoading: false,
    } as unknown as ReturnType<typeof useAnalyticsSummary>)
    mockedUseBreakdowns.mockReturnValue({
      data: BREAKDOWNS,
      isLoading: false,
    } as unknown as ReturnType<typeof useBreakdowns>)
    mockedUseRevenueTimeseries.mockReturnValue({
      data: EMPTY_TIMESERIES,
      isLoading: false,
    } as unknown as ReturnType<typeof useRevenueTimeseries>)
    mockedUseOrders.mockReturnValue({
      data: { data: [], meta: { total_items: 0 } },
      isLoading: false,
    } as unknown as ReturnType<typeof useOrders>)

    renderWithProviders(<OrderBreakdownPage />)

    expect(screen.getByText("Order Breakdown")).toBeInTheDocument()
    expect(screen.getByText("Back to Dashboard")).toBeInTheDocument()

    // Total Orders
    expect(screen.getByText("100")).toBeInTheDocument()
    const totalLink = screen.getByText("Total Orders").closest("a")
    expect(totalLink).toHaveAttribute("href", expect.stringContaining("/orders?"))
    expect(totalLink?.getAttribute("href")).not.toMatch(/status=|payment_type=/)

    // COD: 70/100 = 70.0% -- opens the dedicated COD Orders drill-down
    // (charts + paid/pending), not a plain filtered Orders list.
    expect(screen.getByText("70.0%", { exact: false })).toBeInTheDocument()
    const codLink = screen.getByText("COD Orders").closest("a")
    expect(codLink?.getAttribute("href")).toContain("/orders/breakdown/cod")

    // Prepaid: 30/100 = 30.0%
    const prepaidLink = screen.getByText("Prepaid Orders").closest("a")
    expect(prepaidLink?.getAttribute("href")).toContain("/orders/breakdown/prepaid")

    // Root-cause fix: this page previously had stat cards only, no chart.
    expect(screen.getByText("COD vs Prepaid Orders")).toBeInTheDocument()
    expect(screen.getByText("Orders Timeline")).toBeInTheDocument()

    // Pending: 25/100 = 25.0%, links to status=pending (OrderStatus, not
    // payment_status — the exact distinction the reported bug was about).
    const pendingLink = screen.getByText("Pending Orders").closest("a")
    expect(pendingLink?.getAttribute("href")).toContain("status=pending")
    expect(pendingLink?.getAttribute("href")).not.toContain("payment_status=pending")

    // Fulfilled / Unfulfilled
    expect(screen.getByText("Fulfilled Orders").closest("a")?.getAttribute("href")).toContain(
      "fulfillment_status=fulfilled"
    )
    expect(screen.getByText("Unfulfilled Orders").closest("a")?.getAttribute("href")).toContain(
      "fulfillment_status=unfulfilled"
    )

    // Cancelled — derived from the order_status breakdown (count 5/100 = 5.0%)
    const cancelledLink = screen.getByText("Cancelled Orders").closest("a")
    expect(cancelledLink?.getAttribute("href")).toContain("status=cancelled")
  })

  it("shows skeletons while loading instead of zeros", () => {
    mockedUseSummary.mockReturnValue({
      data: undefined,
      isLoading: true,
    } as unknown as ReturnType<typeof useAnalyticsSummary>)
    mockedUseBreakdowns.mockReturnValue({
      data: undefined,
      isLoading: true,
    } as unknown as ReturnType<typeof useBreakdowns>)
    mockedUseRevenueTimeseries.mockReturnValue({
      data: undefined,
      isLoading: true,
    } as unknown as ReturnType<typeof useRevenueTimeseries>)
    mockedUseOrders.mockReturnValue({
      data: undefined,
      isLoading: true,
    } as unknown as ReturnType<typeof useOrders>)

    const { container } = renderWithProviders(<OrderBreakdownPage />)
    expect(container.querySelectorAll("[data-slot='skeleton']").length).toBeGreaterThan(0)
  })
})

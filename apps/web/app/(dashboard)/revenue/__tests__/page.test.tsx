import { describe, expect, it, vi } from "vitest"
import { screen } from "@testing-library/react"

import { renderWithProviders } from "@/test-utils/render-with-providers"
import RevenueAnalyticsPage from "@/app/(dashboard)/revenue/page"
import { useAnalyticsSummary, useRevenueTimeseries } from "@/services/analytics"
import { useOrders } from "@/services/orders"
import type { AnalyticsSummary, RevenueTimeseries } from "@/types/analytics"

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn() }),
  usePathname: () => "/revenue",
  useSearchParams: () => new URLSearchParams(),
}))

vi.mock("@/services/analytics", () => ({
  useAnalyticsSummary: vi.fn(),
  useRevenueTimeseries: vi.fn(),
}))

vi.mock("@/services/orders", () => ({
  useOrders: vi.fn(),
}))

const mockedUseSummary = vi.mocked(useAnalyticsSummary)
const mockedUseRevenueTimeseries = vi.mocked(useRevenueTimeseries)
const mockedUseOrders = vi.mocked(useOrders)

function kpi(current: string) {
  return { current, previous: "0", change_pct: null }
}

const SUMMARY: AnalyticsSummary = {
  date_from: "2026-08-01T00:00:00Z",
  date_to: "2026-08-29T00:00:00Z",
  total_orders: kpi("100"),
  total_revenue: kpi("125000.00"),
  total_customers: kpi("40"),
  total_products: kpi("10"),
  fulfilled_orders: kpi("60"),
  unfulfilled_orders: kpi("40"),
  cod_orders: kpi("70"),
  prepaid_orders: kpi("30"),
  pending_orders: kpi("25"),
  cod_value: kpi("75000.00"),
  prepaid_value: kpi("50000.00"),
  delivered_shipments: kpi("50"),
  in_transit_shipments: kpi("5"),
  out_for_delivery_shipments: kpi("2"),
  delayed_shipments: kpi("1"),
  open_ndr: kpi("3"),
  open_rto: kpi("1"),
  returns: kpi("2"),
  refunds: kpi("1"),
}

const TIMESERIES: RevenueTimeseries = {
  interval: "day",
  points: [
    {
      bucket: "2026-08-15",
      cod_orders: 5,
      cod_revenue: "3000.00",
      prepaid_orders: 3,
      prepaid_revenue: "2000.00",
      total_orders: 8,
      total_revenue: "5000.00",
    },
  ],
}

describe("RevenueAnalyticsPage", () => {
  it("shows Total/COD/Prepaid revenue with correct percentages, chart titles, and drill-down links", () => {
    mockedUseSummary.mockReturnValue({
      data: SUMMARY,
      isLoading: false,
    } as unknown as ReturnType<typeof useAnalyticsSummary>)
    mockedUseRevenueTimeseries.mockReturnValue({
      data: TIMESERIES,
      isLoading: false,
    } as unknown as ReturnType<typeof useRevenueTimeseries>)
    mockedUseOrders.mockReturnValue({
      data: { data: [], meta: { total_items: 0 } },
      isLoading: false,
    } as unknown as ReturnType<typeof useOrders>)

    renderWithProviders(<RevenueAnalyticsPage />)

    expect(screen.getByText("Revenue Analytics")).toBeInTheDocument()
    expect(screen.getByText("Back to Dashboard")).toBeInTheDocument()

    // Cards: 1: total revenue matches the summary's total_revenue exactly
    // -- cards and chart pull from the same date-scoped backend dataset.
    expect(screen.getByText("₹1,25,000.00")).toBeInTheDocument()
    expect(screen.getByText("₹75,000.00")).toBeInTheDocument()
    expect(screen.getByText("₹50,000.00")).toBeInTheDocument()
    // COD: 75000/125000 = 60.0%, Prepaid: 50000/125000 = 40.0% -- appears
    // in both the stat card and the donut legend (same dataset, matching
    // percentage in both places).
    expect(screen.getAllByText("60.0%", { exact: false }).length).toBeGreaterThan(0)
    expect(screen.getAllByText("40.0%", { exact: false }).length).toBeGreaterThan(0)

    // 2: real graphical representation, not just cards/tables.
    expect(screen.getByText("COD vs Prepaid Revenue")).toBeInTheDocument()
    expect(screen.getByText("Revenue Timeline")).toBeInTheDocument()

    // 3/5: clicking through to COD/Prepaid Revenue drill-downs.
    const codLink = screen.getByText("COD Revenue").closest("a")
    expect(codLink?.getAttribute("href")).toContain("/revenue/cod")
    const prepaidLink = screen.getByText("Prepaid Revenue").closest("a")
    expect(prepaidLink?.getAttribute("href")).toContain("/revenue/prepaid")
  })

  it("shows skeletons while loading instead of zeros", () => {
    mockedUseSummary.mockReturnValue({
      data: undefined,
      isLoading: true,
    } as unknown as ReturnType<typeof useAnalyticsSummary>)
    mockedUseRevenueTimeseries.mockReturnValue({
      data: undefined,
      isLoading: true,
    } as unknown as ReturnType<typeof useRevenueTimeseries>)
    mockedUseOrders.mockReturnValue({
      data: undefined,
      isLoading: true,
    } as unknown as ReturnType<typeof useOrders>)

    const { container } = renderWithProviders(<RevenueAnalyticsPage />)
    expect(container.querySelectorAll("[data-slot='skeleton']").length).toBeGreaterThan(0)
  })
})

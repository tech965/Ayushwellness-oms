import { describe, expect, it, vi } from "vitest"
import { screen } from "@testing-library/react"

import { renderWithProviders } from "@/test-utils/render-with-providers"
import CodOrdersPage from "@/app/(dashboard)/orders/breakdown/cod/page"
import { usePaymentStatusBreakdown, usePaymentStatusTimeseries } from "@/services/analytics"
import { useOrders } from "@/services/orders"
import type { PaymentStatusBreakdown, PaymentStatusTimeseries } from "@/types/analytics"

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn() }),
  usePathname: () => "/orders/breakdown/cod",
  useSearchParams: () => new URLSearchParams(),
}))

vi.mock("@/services/analytics", () => ({
  usePaymentStatusBreakdown: vi.fn(),
  usePaymentStatusTimeseries: vi.fn(),
}))

vi.mock("@/services/orders", () => ({
  useOrders: vi.fn(),
}))

const mockedUseBreakdown = vi.mocked(usePaymentStatusBreakdown)
const mockedUseTimeseries = vi.mocked(usePaymentStatusTimeseries)
const mockedUseOrders = vi.mocked(useOrders)

const BREAKDOWN: PaymentStatusBreakdown = {
  payment_type: "cod",
  total_count: 20,
  total_revenue: "75000.00",
  paid_count: 15,
  paid_revenue: "60000.00",
  pending_count: 5,
  pending_revenue: "15000.00",
  items: [],
}

const TIMESERIES: PaymentStatusTimeseries = { interval: "day", payment_type: "cod", points: [] }

describe("CodOrdersPage", () => {
  it("shows Total/Paid/Pending COD order COUNTS (not revenue) with correct percentages", () => {
    mockedUseBreakdown.mockReturnValue({
      data: BREAKDOWN,
      isLoading: false,
    } as unknown as ReturnType<typeof usePaymentStatusBreakdown>)
    mockedUseTimeseries.mockReturnValue({
      data: TIMESERIES,
      isLoading: false,
    } as unknown as ReturnType<typeof usePaymentStatusTimeseries>)
    mockedUseOrders.mockReturnValue({
      data: { data: [], meta: { total_items: 0 } },
      isLoading: false,
    } as unknown as ReturnType<typeof useOrders>)

    renderWithProviders(<CodOrdersPage />)

    expect(screen.getByRole("heading", { name: "COD Orders" })).toBeInTheDocument()
    expect(screen.getByText("Back to Order Breakdown")).toBeInTheDocument()

    // 9: counts, not money -- distinct from the COD Revenue drill-down's
    // ₹ figures even though both derive from the same underlying data.
    // (Some counts also appear in the donut's center total, hence
    // getAllByText -- still proving the number is genuinely rendered.)
    expect(screen.getAllByText("20").length).toBeGreaterThan(0)
    expect(screen.getAllByText("15").length).toBeGreaterThan(0)
    expect(screen.getAllByText("5").length).toBeGreaterThan(0)
    expect(screen.queryByText("₹75,000.00")).not.toBeInTheDocument()
    // Paid: 15/20 = 75.0%, Pending: 5/20 = 25.0%
    expect(screen.getAllByText("75.0%", { exact: false }).length).toBeGreaterThan(0)
    expect(screen.getAllByText("25.0%", { exact: false }).length).toBeGreaterThan(0)

    expect(screen.getByText("Paid vs Pending COD Orders")).toBeInTheDocument()
    expect(screen.getByText("COD Orders Timeline")).toBeInTheDocument()
  })
})

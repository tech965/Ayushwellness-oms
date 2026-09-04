import { describe, expect, it, vi } from "vitest"
import { screen } from "@testing-library/react"

import { renderWithProviders } from "@/test-utils/render-with-providers"
import CodRevenuePage from "@/app/(dashboard)/revenue/cod/page"
import { usePaymentStatusBreakdown, usePaymentStatusTimeseries } from "@/services/analytics"
import { useOrders } from "@/services/orders"
import type { PaymentStatusBreakdown, PaymentStatusTimeseries } from "@/types/analytics"

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn() }),
  usePathname: () => "/revenue/cod",
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
  total_count: 10,
  total_revenue: "75000.00",
  paid_count: 8,
  paid_revenue: "60000.00",
  pending_count: 2,
  pending_revenue: "15000.00",
  items: [
    { status: "paid", count: 8, revenue: "60000.00" },
    { status: "pending", count: 2, revenue: "15000.00" },
  ],
}

const TIMESERIES: PaymentStatusTimeseries = {
  interval: "day",
  payment_type: "cod",
  points: [],
}

describe("CodRevenuePage", () => {
  it("shows Total/Paid/Pending COD revenue with correct percentages and links back through the drill-down chain", () => {
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

    renderWithProviders(<CodRevenuePage />)

    expect(screen.getByText("COD Revenue")).toBeInTheDocument()
    // 14: back navigation goes to the parent drill-down level (Revenue
    // Analytics), never straight to Dashboard.
    expect(screen.getByText("Back to Revenue Analytics")).toBeInTheDocument()

    expect(screen.getByText("₹75,000.00")).toBeInTheDocument()
    expect(screen.getByText("₹60,000.00")).toBeInTheDocument()
    expect(screen.getByText("₹15,000.00")).toBeInTheDocument()
    // Paid: 60000/75000 = 80.0%, Pending: 15000/75000 = 20.0%
    expect(screen.getAllByText("80.0%", { exact: false }).length).toBeGreaterThan(0)
    expect(screen.getAllByText("20.0%", { exact: false }).length).toBeGreaterThan(0)

    expect(screen.getByText("Paid vs Pending COD")).toBeInTheDocument()
    expect(screen.getByText("COD Revenue Timeline")).toBeInTheDocument()

    // 12: clickable segments apply the right filter -- Paid links straight
    // to the existing Orders page pre-filtered by payment_type + status.
    const paidLink = screen.getByText("Paid COD").closest("a")
    expect(paidLink?.getAttribute("href")).toContain("payment_type=cod")
    expect(paidLink?.getAttribute("href")).toContain("payment_status=paid")

    // Pending COD now drills into the Fulfilled/Unfulfilled breakdown
    // first, instead of going straight to Orders (the one behavior change
    // this request makes -- Prepaid's Pending is unaffected, see the
    // prepaid page test).
    const pendingLink = screen.getByText("Pending COD").closest("a")
    expect(pendingLink?.getAttribute("href")).toContain("/revenue/cod/pending")
  })
})

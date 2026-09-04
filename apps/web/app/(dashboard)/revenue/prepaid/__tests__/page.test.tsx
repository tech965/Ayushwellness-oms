import { describe, expect, it, vi } from "vitest"
import { screen } from "@testing-library/react"

import { renderWithProviders } from "@/test-utils/render-with-providers"
import PrepaidRevenuePage from "@/app/(dashboard)/revenue/prepaid/page"
import { usePaymentStatusBreakdown, usePaymentStatusTimeseries } from "@/services/analytics"
import { useOrders } from "@/services/orders"
import type { PaymentStatusBreakdown, PaymentStatusTimeseries } from "@/types/analytics"

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn() }),
  usePathname: () => "/revenue/prepaid",
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
  payment_type: "prepaid",
  total_count: 10,
  total_revenue: "50000.00",
  paid_count: 9,
  paid_revenue: "45000.00",
  pending_count: 1,
  pending_revenue: "5000.00",
  items: [
    { status: "paid", count: 9, revenue: "45000.00" },
    { status: "pending", count: 1, revenue: "5000.00" },
  ],
}

const TIMESERIES: PaymentStatusTimeseries = {
  interval: "day",
  payment_type: "prepaid",
  points: [],
}

describe("PrepaidRevenuePage", () => {
  it("keeps Pending Prepaid going straight to Orders, unlike Pending COD", () => {
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

    renderWithProviders(<PrepaidRevenuePage />)

    const pendingLink = screen.getByText("Pending Prepaid").closest("a")
    expect(pendingLink?.getAttribute("href")).toContain("/orders?")
    expect(pendingLink?.getAttribute("href")).toContain("payment_type=prepaid")
    expect(pendingLink?.getAttribute("href")).toContain("payment_status=pending")
    expect(pendingLink?.getAttribute("href")).not.toContain("/revenue/cod/pending")
  })
})

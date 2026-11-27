import { describe, expect, it, vi } from "vitest"
import { screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { renderWithProviders } from "@/test-utils/render-with-providers"
import { CodDashboardContent } from "@/components/analytics/cod-dashboard-content"
import { useCodDashboard, useFulfillmentPaymentOrders } from "@/services/analytics"
import type { CodDashboard } from "@/types/analytics"

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn() }),
  usePathname: () => "/orders/breakdown/cod-dashboard",
  useSearchParams: () => new URLSearchParams(),
}))

vi.mock("@/services/analytics", () => ({
  useCodDashboard: vi.fn(),
  useFulfillmentPaymentOrders: vi.fn(),
}))

const mockedUseCodDashboard = vi.mocked(useCodDashboard)
const mockedUseOrders = vi.mocked(useFulfillmentPaymentOrders)

function kpi(order_count: number, amount: string, percent_of_total_cod_amount: number) {
  return { order_count, amount, percent_of_total_cod_amount }
}

const DATA: CodDashboard = {
  date_from: "2026-08-01T00:00:00Z",
  date_to: "2026-08-29T00:00:00Z",
  total_cod: kpi(100, "1000000.00", 100),
  fulfilled_cod: kpi(60, "600000.00", 60),
  unfulfilled_cod: kpi(40, "400000.00", 40),
  delivered_cod: kpi(55, "550000.00", 55),
}

/** Root-cause regression: production testing asked to verify every COD
 * KPI opens the order list that actually matches it. "Delivered COD"
 * specifically must filter on `unified_status=delivered` in addition to
 * `payment_type=cod` -- filtering on payment_type alone would show every
 * COD order, not just the delivered ones.
 */
describe("CodDashboardContent KPI drill-down filters", () => {
  function setup() {
    mockedUseCodDashboard.mockReturnValue({
      data: DATA,
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useCodDashboard>)
    mockedUseOrders.mockReturnValue({
      data: { data: [], meta: { total_items: 0 } },
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useFulfillmentPaymentOrders>)
  }

  function lastCallParams() {
    const calls = mockedUseOrders.mock.calls
    return calls[calls.length - 1][0]
  }

  it("Total COD drills down with payment_type=cod only", async () => {
    setup()
    renderWithProviders(<CodDashboardContent />)
    await userEvent.click(screen.getByText("Total COD"))
    const params = lastCallParams()
    expect(params.paymentType).toBe("cod")
    expect(params.fulfillmentStatus).toBeUndefined()
    expect(params.unifiedStatus).toBeUndefined()
  })

  it("Fulfilled COD drills down with payment_type=cod and fulfillment_status=fulfilled", async () => {
    setup()
    renderWithProviders(<CodDashboardContent />)
    await userEvent.click(screen.getByText("Fulfilled COD"))
    const params = lastCallParams()
    expect(params.paymentType).toBe("cod")
    expect(params.fulfillmentStatus).toBe("fulfilled")
    expect(params.unifiedStatus).toBeUndefined()
  })

  it("Unfulfilled COD drills down with payment_type=cod and fulfillment_status=unfulfilled", async () => {
    setup()
    renderWithProviders(<CodDashboardContent />)
    await userEvent.click(screen.getByText("Unfulfilled COD"))
    const params = lastCallParams()
    expect(params.paymentType).toBe("cod")
    expect(params.fulfillmentStatus).toBe("unfulfilled")
    expect(params.unifiedStatus).toBeUndefined()
  })

  it("Delivered COD drills down with payment_type=cod and unified_status=delivered, not all COD orders", async () => {
    setup()
    renderWithProviders(<CodDashboardContent />)
    await userEvent.click(screen.getByText("Delivered COD"))
    const params = lastCallParams()
    expect(params.paymentType).toBe("cod")
    expect(params.unifiedStatus).toBe("delivered")
    expect(params.fulfillmentStatus).toBeUndefined()
  })

  it("clicking a second KPI replaces the first selection rather than mixing filters", async () => {
    setup()
    renderWithProviders(<CodDashboardContent />)
    await userEvent.click(screen.getByText("Fulfilled COD"))
    await userEvent.click(screen.getByText("Delivered COD"))
    const params = lastCallParams()
    expect(params.unifiedStatus).toBe("delivered")
    expect(params.fulfillmentStatus).toBeUndefined()
  })
})

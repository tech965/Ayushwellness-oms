import { describe, expect, it, vi } from "vitest"
import { screen } from "@testing-library/react"

import { renderWithProviders } from "@/test-utils/render-with-providers"
import MarketplaceDashboardPage from "@/app/(dashboard)/marketplace/dashboard/page"
import { useMarketplaceDashboardSummary } from "@/services/marketplace"

const mockSearchParams = new URLSearchParams()

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn() }),
  usePathname: () => "/marketplace/dashboard",
  useSearchParams: () => mockSearchParams,
}))

vi.mock("@/services/marketplace", () => ({
  useMarketplaceDashboardSummary: vi.fn(),
  useMarketplaceOrders: vi.fn(),
}))

const mockedUseSummary = vi.mocked(useMarketplaceDashboardSummary)

function kpi(current: string) {
  return { current, previous: "0", change_pct: null }
}

describe("MarketplaceDashboardPage", () => {
  it("shows skeletons while loading", () => {
    mockedUseSummary.mockReturnValue({
      isLoading: true,
      isError: false,
      error: null,
      data: undefined,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useMarketplaceDashboardSummary>)
    const { container } = renderWithProviders(<MarketplaceDashboardPage />)
    expect(container.querySelectorAll("[data-slot='skeleton']").length).toBeGreaterThan(0)
  })

  it("renders a zeroed dashboard honestly when no marketplace orders exist yet (Phase 1, no live sync)", () => {
    mockedUseSummary.mockReturnValue({
      isLoading: false,
      isError: false,
      error: null,
      data: {
        date_from: "2026-10-06T00:00:00Z",
        date_to: "2026-11-05T00:00:00Z",
        total_orders: kpi("0"),
        amazon_orders: kpi("0"),
        flipkart_orders: kpi("0"),
        meesho_orders: kpi("0"),
        total_sales: kpi("0"),
        pending_orders: kpi("0"),
        unfulfilled_orders: kpi("0"),
        shipped_orders: kpi("0"),
        delivered_orders: kpi("0"),
        cancelled_orders: kpi("0"),
        cod_orders: kpi("0"),
        prepaid_orders: kpi("0"),
      },
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useMarketplaceDashboardSummary>)

    renderWithProviders(<MarketplaceDashboardPage />)
    expect(screen.getByText("Total Marketplace Orders")).toBeInTheDocument()
    expect(screen.getByText("Amazon Orders")).toBeInTheDocument()
    expect(screen.getByText("Flipkart Orders")).toBeInTheDocument()
    expect(screen.getByText("Meesho Orders")).toBeInTheDocument()
  })

  it("renders real per-marketplace counts without conflating them", () => {
    mockedUseSummary.mockReturnValue({
      isLoading: false,
      isError: false,
      error: null,
      data: {
        date_from: "2026-10-06T00:00:00Z",
        date_to: "2026-11-05T00:00:00Z",
        total_orders: kpi("312"),
        amazon_orders: kpi("115"),
        flipkart_orders: kpi("109"),
        meesho_orders: kpi("88"),
        total_sales: kpi("45000"),
        pending_orders: kpi("21"),
        unfulfilled_orders: kpi("17"),
        shipped_orders: kpi("142"),
        delivered_orders: kpi("151"),
        cancelled_orders: kpi("8"),
        cod_orders: kpi("201"),
        prepaid_orders: kpi("111"),
      },
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useMarketplaceDashboardSummary>)

    renderWithProviders(<MarketplaceDashboardPage />)
    expect(screen.getByText("312")).toBeInTheDocument()
    expect(screen.getByText("115")).toBeInTheDocument()
    expect(screen.getByText("109")).toBeInTheDocument()
    expect(screen.getByText("88")).toBeInTheDocument()
  })
})

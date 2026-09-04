import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { screen } from "@testing-library/react"

import { renderWithProviders } from "@/test-utils/render-with-providers"
import DashboardPage from "@/app/(dashboard)/dashboard/page"
import { useAuth } from "@/lib/auth-context"
import { istEndOfDay, istStartOfDay } from "@/lib/ist-date"
import {
  useAnalyticsSummary,
  useBreakdowns,
  useCourierPerformance,
  useOrdersTimeseries,
  useRecentActivity,
  useReturnsRefundsSummary,
  useTopProducts,
} from "@/services/analytics"

let searchParams = new URLSearchParams()

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn() }),
  usePathname: () => "/dashboard",
  useSearchParams: () => searchParams,
}))

vi.mock("@/lib/auth-context", () => ({
  useAuth: vi.fn(),
}))

vi.mock("@/services/analytics", () => ({
  useAnalyticsSummary: vi.fn(),
  useBreakdowns: vi.fn(),
  useCourierPerformance: vi.fn(),
  useOrdersTimeseries: vi.fn(),
  useRecentActivity: vi.fn(),
  useReturnsRefundsSummary: vi.fn(),
  useTopProducts: vi.fn(),
}))

const mockedUseAuth = vi.mocked(useAuth)
const mockedTimeseries = vi.mocked(useOrdersTimeseries)
const mockedSummary = vi.mocked(useAnalyticsSummary)
const mockedBreakdowns = vi.mocked(useBreakdowns)
const mockedCourier = vi.mocked(useCourierPerformance)
const mockedRecent = vi.mocked(useRecentActivity)
const mockedReturnsRefunds = vi.mocked(useReturnsRefundsSummary)
const mockedTopProducts = vi.mocked(useTopProducts)

const EMPTY_TIMESERIES = { interval: "hour" as const, points: [] }

function setUrlDateRange(from: Date, to: Date) {
  searchParams = new URLSearchParams({
    date_from: from.toISOString(),
    date_to: to.toISOString(),
  })
}

describe("Dashboard Today/Yesterday hourly comparison", () => {
  beforeEach(() => {
    mockedUseAuth.mockReturnValue({
      user: {
        id: "1",
        name: "Test",
        email: "t@example.com",
        phone: null,
        is_active: true,
        is_superuser: false,
        roles: [],
        permissions: [],
      },
      permissions: new Set(),
      isLoading: false,
      hasPermission: () => true,
      hasRole: () => false,
      logout: vi.fn(),
    } as unknown as ReturnType<typeof useAuth>)

    mockedSummary.mockReturnValue({ data: undefined, isLoading: false } as unknown as ReturnType<
      typeof useAnalyticsSummary
    >)
    mockedBreakdowns.mockReturnValue({ data: undefined, isLoading: false } as unknown as ReturnType<
      typeof useBreakdowns
    >)
    mockedCourier.mockReturnValue({ data: undefined, isLoading: false } as unknown as ReturnType<
      typeof useCourierPerformance
    >)
    mockedRecent.mockReturnValue({ data: undefined, isLoading: false } as unknown as ReturnType<
      typeof useRecentActivity
    >)
    mockedReturnsRefunds.mockReturnValue({
      data: undefined,
      isLoading: false,
    } as unknown as ReturnType<typeof useReturnsRefundsSummary>)
    mockedTopProducts.mockReturnValue({ data: undefined, isLoading: false } as unknown as ReturnType<
      typeof useTopProducts
    >)
    mockedTimeseries.mockReturnValue({
      data: EMPTY_TIMESERIES,
      isLoading: false,
    } as unknown as ReturnType<typeof useOrdersTimeseries>)
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  // TEST 6: IST boundary correctness -- 19:30 UTC on Sep 2 is already
  // 01:00 AM IST on Sep 3. A naive UTC-day check would misclassify
  // "today" as Sep 2; the real IST calendar day is Sep 3.
  const NOW_UTC = new Date("2026-09-02T19:30:00.000Z") // = 2026-09-03 01:00 IST

  it("TEST 1: Today selected shows 'Today vs. Yesterday'", () => {
    vi.useFakeTimers()
    vi.setSystemTime(NOW_UTC)
    setUrlDateRange(istStartOfDay(NOW_UTC), istEndOfDay(NOW_UTC))

    renderWithProviders(<DashboardPage />)

    expect(screen.getByText("Today vs. Yesterday")).toBeInTheDocument()
  })

  it("TEST 2/3: Yesterday selected shows 'Yesterday vs. <day before>', never 'vs. Today'", () => {
    vi.useFakeTimers()
    vi.setSystemTime(NOW_UTC)
    const yesterday = new Date(NOW_UTC.getTime() - 24 * 60 * 60 * 1000)
    setUrlDateRange(istStartOfDay(yesterday), istEndOfDay(yesterday))

    renderWithProviders(<DashboardPage />)

    // IST calendar day for NOW_UTC is Sep 3, so "yesterday" is Sep 2 and
    // the day before that is Sep 1.
    expect(screen.getByText("Yesterday vs. 1 Sep")).toBeInTheDocument()
    expect(screen.queryByText(/vs\. Today/)).not.toBeInTheDocument()
  })

  it("TEST 6: IST boundary -- late-UTC-evening/early-IST-morning instant still resolves Today correctly", () => {
    vi.useFakeTimers()
    vi.setSystemTime(NOW_UTC) // 01:00 AM IST Sep 3
    setUrlDateRange(istStartOfDay(NOW_UTC), istEndOfDay(NOW_UTC))

    renderWithProviders(<DashboardPage />)

    // If the boundary were computed in naive UTC, this would render the
    // plain "Orders & Revenue" chart (no comparison) instead.
    expect(screen.getByText("Today vs. Yesterday")).toBeInTheDocument()
  })

  it("TEST 8: This Week (multi-day) range keeps the existing plain chart, no comparison", () => {
    vi.useFakeTimers()
    vi.setSystemTime(NOW_UTC)
    const weekAgo = new Date(NOW_UTC.getTime() - 6 * 24 * 60 * 60 * 1000)
    setUrlDateRange(istStartOfDay(weekAgo), istEndOfDay(NOW_UTC))

    renderWithProviders(<DashboardPage />)

    expect(screen.getByText("Orders & Revenue")).toBeInTheDocument()
    expect(screen.queryByText(/vs\./)).not.toBeInTheDocument()
  })
})

import { describe, expect, it } from "vitest"
import { screen } from "@testing-library/react"

import { renderWithProviders } from "@/test-utils/render-with-providers"
import {
  buildHourlyComparisonData,
  OrdersRevenueChart,
} from "@/components/dashboard/orders-revenue-chart"
import type { OrdersTimeseries } from "@/types/analytics"

function timeseries(points: { hour: number; orders: number; revenue: string }[]): OrdersTimeseries {
  return {
    interval: "hour",
    points: points.map((p) => ({
      bucket: `2026-09-02T${String(p.hour).padStart(2, "0")}:00`,
      order_count: p.orders,
      revenue: p.revenue,
    })),
  }
}

describe("buildHourlyComparisonData", () => {
  it("TEST 4/5: produces all 24 hourly buckets, blanking future hours on the primary day only", () => {
    const primary = timeseries([{ hour: 3, orders: 5, revenue: "500.00" }])
    const previous = timeseries([{ hour: 3, orders: 2, revenue: "200.00" }])

    // currentIstHour = 3 -- hours 0-3 have elapsed, 4-23 have not.
    const result = buildHourlyComparisonData(primary, previous, 3)

    expect(result).toHaveLength(24)
    expect(result[3].orders).toBe(5)
    expect(result[4].orders).toBeNull() // future hour on the primary day -- never a fake zero
    expect(result[4].revenue).toBeNull()
    // The comparison ("previous") series is a fully-elapsed past day --
    // every hour shows real data (including genuine zeros), never null.
    expect(result[3].ordersPrevious).toBe(2)
    expect(result[4].ordersPrevious).toBe(0)
  })

  it("a fully-elapsed primary day (currentIstHour=23) shows real data or genuine zero in every hour", () => {
    const primary = timeseries([{ hour: 10, orders: 7, revenue: "700.00" }])
    const result = buildHourlyComparisonData(primary, undefined, 23)

    expect(result[10].orders).toBe(7)
    expect(result[23].orders).toBe(0) // genuine zero, not null -- the day is over
    expect(result.every((point) => point.orders !== null)).toBe(true)
  })

  it("missing primary/previous data never crashes and never fabricates non-zero values", () => {
    const result = buildHourlyComparisonData(undefined, undefined, 23)
    expect(result).toHaveLength(24)
    expect(result.every((point) => point.orders === 0)).toBe(true)
    expect(result.every((point) => point.ordersPrevious === 0)).toBe(true)
  })
})

describe("OrdersRevenueChart comparison labels", () => {
  const EMPTY: OrdersTimeseries = { interval: "hour", points: [] }

  it("TEST 7: Today mode still reads exactly 'Today vs. Yesterday' (existing behavior unchanged)", () => {
    renderWithProviders(
      <OrdersRevenueChart
        data={EMPTY}
        interval="hour"
        onIntervalChange={() => {}}
        isLoading={false}
        comparison={{
          previous: EMPTY,
          isLoading: false,
          currentIstHour: 12,
          primaryLabel: "Today",
          previousLabel: "Yesterday",
        }}
      />
    )
    expect(screen.getByText("Today vs. Yesterday")).toBeInTheDocument()
    expect(screen.getByText("Orders (Today)")).toBeInTheDocument()
    expect(screen.getByText("Orders (Yesterday)")).toBeInTheDocument()
  })

  it("TEST 2/3: Yesterday mode reads 'Yesterday vs. <date>', never 'Yesterday vs. Today'", () => {
    renderWithProviders(
      <OrdersRevenueChart
        data={EMPTY}
        interval="hour"
        onIntervalChange={() => {}}
        isLoading={false}
        comparison={{
          previous: EMPTY,
          isLoading: false,
          currentIstHour: 23,
          primaryLabel: "Yesterday",
          previousLabel: "1 Sep",
        }}
      />
    )
    expect(screen.getByText("Yesterday vs. 1 Sep")).toBeInTheDocument()
    expect(screen.getByText("Orders (Yesterday)")).toBeInTheDocument()
    expect(screen.getByText("Orders (1 Sep)")).toBeInTheDocument()
    expect(screen.queryByText(/vs\. Today/)).not.toBeInTheDocument()
    expect(screen.queryByText("Orders (Today)")).not.toBeInTheDocument()
  })

  it("non-comparison mode (e.g. This Week/Custom Range) is completely unaffected", () => {
    renderWithProviders(
      <OrdersRevenueChart
        data={timeseries([{ hour: 0, orders: 3, revenue: "300.00" }])}
        interval="day"
        onIntervalChange={() => {}}
        isLoading={false}
      />
    )
    expect(screen.getByText("Orders & Revenue")).toBeInTheDocument()
    expect(screen.queryByText(/vs\./)).not.toBeInTheDocument()
  })

  it("shows a dynamic empty state naming both compared days", () => {
    renderWithProviders(
      <OrdersRevenueChart
        data={EMPTY}
        interval="hour"
        onIntervalChange={() => {}}
        isLoading={false}
        comparison={{
          previous: EMPTY,
          isLoading: false,
          currentIstHour: 23,
          primaryLabel: "Yesterday",
          previousLabel: "1 Sep",
        }}
      />
    )
    expect(screen.getByText("No orders on Yesterday or 1 Sep.")).toBeInTheDocument()
  })
})

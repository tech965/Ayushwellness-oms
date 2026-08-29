"use client"

import * as React from "react"
import {
  Ban,
  Clock,
  IndianRupee,
  PackageCheck,
  PackageX,
  ShoppingCart,
  Wallet,
} from "lucide-react"
import { endOfDay, startOfDay, subDays } from "date-fns"

import { DrilldownOrdersTable } from "@/components/analytics/drilldown-orders-table"
import { SplitTimelineChart } from "@/components/analytics/split-timeline-chart"
import { StatusDonutCard } from "@/components/dashboard/status-donut-card"
import { PageHeader } from "@/components/shared/page-header"
import { StatTile } from "@/components/shared/stat-tile"
import { Skeleton } from "@/components/ui/skeleton"
import { formatDate, formatMoney } from "@/lib/format"
import { useUrlFilters } from "@/lib/use-url-filters"
import { useAnalyticsSummary, useBreakdowns, useRevenueTimeseries } from "@/services/analytics"

const FILTER_DEFAULTS = { date_from: "", date_to: "" }

function pct(part: number, total: number): string {
  if (total <= 0) return "0%"
  return `${((part / total) * 100).toFixed(1)}%`
}

function BreakdownSkeleton() {
  return (
    <>
      <PageHeader title="Order Breakdown" backHref="/dashboard" backLabel="Back to Dashboard" />
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        {Array.from({ length: 8 }).map((_, i) => (
          <Skeleton key={i} className="h-28 w-full" />
        ))}
      </div>
    </>
  )
}

export default function OrderBreakdownPage() {
  return (
    <React.Suspense fallback={<BreakdownSkeleton />}>
      <OrderBreakdownContent />
    </React.Suspense>
  )
}

function OrderBreakdownContent() {
  const { filters } = useUrlFilters(FILTER_DEFAULTS)

  // Same resolution/default window as the Dashboard (last 30 days) so a
  // direct visit to this page (not via the drill-down link) still shows a
  // sensible, self-consistent range — and matches exactly when arriving
  // via the "Total Orders" KPI, which passes its own resolved dates.
  const resolvedFrom = filters.date_from
    ? new Date(filters.date_from)
    : startOfDay(subDays(new Date(), 29))
  const resolvedTo = filters.date_to ? new Date(filters.date_to) : endOfDay(new Date())

  const dateParams = {
    date_from: resolvedFrom.toISOString(),
    date_to: resolvedTo.toISOString(),
  }

  const summaryQuery = useAnalyticsSummary(dateParams)
  const breakdownsQuery = useBreakdowns(dateParams)
  const timeseriesQuery = useRevenueTimeseries({ ...dateParams, interval: "day" })
  const summary = summaryQuery.data

  const ordersTimelineData = (timeseriesQuery.data?.points ?? []).map((p) => ({
    bucket: p.bucket,
    seriesAValue: p.cod_orders,
    seriesBValue: p.prepaid_orders,
  }))

  const orderQueryString = new URLSearchParams(dateParams).toString()
  function ordersHref(extra: Record<string, string>): string {
    const params = new URLSearchParams(orderQueryString)
    for (const [key, value] of Object.entries(extra)) params.set(key, value)
    return `/orders?${params.toString()}`
  }

  const totalOrders = summary ? Number(summary.total_orders.current) : 0
  const cancelledCount =
    breakdownsQuery.data?.order_status.find((row) => row.status === "cancelled")?.count ?? 0

  const isLoading = summaryQuery.isLoading

  return (
    <>
      <PageHeader
        title="Order Breakdown"
        description={`${formatDate(resolvedFrom.toISOString())} – ${formatDate(resolvedTo.toISOString())}`}
        backHref="/dashboard"
        backLabel="Back to Dashboard"
      />

      {isLoading ? (
        <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
          {Array.from({ length: 8 }).map((_, i) => (
            <Skeleton key={i} className="h-28 w-full" />
          ))}
        </div>
      ) : (
        <div className="flex flex-col gap-6">
          <section className="grid grid-cols-2 gap-4 lg:grid-cols-4">
            <StatTile
              label="Total Orders"
              icon={ShoppingCart}
              value={totalOrders.toLocaleString("en-IN")}
              subtext={`Total value: ${formatMoney(summary?.total_revenue.current ?? "0")}`}
              accent="blue"
              href={ordersHref({})}
            />
            <StatTile
              label="COD Orders"
              icon={Wallet}
              value={summary ? Number(summary.cod_orders.current).toLocaleString("en-IN") : 0}
              subtext={`${pct(Number(summary?.cod_orders.current ?? 0), totalOrders)} · ${formatMoney(summary?.cod_value.current ?? "0")}`}
              accent="amber"
              href={`/orders/breakdown/cod?${orderQueryString}`}
            />
            <StatTile
              label="Prepaid Orders"
              icon={IndianRupee}
              value={
                summary ? Number(summary.prepaid_orders.current).toLocaleString("en-IN") : 0
              }
              subtext={`${pct(Number(summary?.prepaid_orders.current ?? 0), totalOrders)} · ${formatMoney(summary?.prepaid_value.current ?? "0")}`}
              accent="emerald"
              href={`/orders/breakdown/prepaid?${orderQueryString}`}
            />
            <StatTile
              label="Pending Orders"
              icon={Clock}
              value={
                summary ? Number(summary.pending_orders.current).toLocaleString("en-IN") : 0
              }
              subtext={pct(Number(summary?.pending_orders.current ?? 0), totalOrders)}
              accent="orange"
              href={ordersHref({ status: "pending" })}
            />
          </section>

          <section className="grid grid-cols-2 gap-4 lg:grid-cols-3">
            <StatTile
              label="Fulfilled Orders"
              icon={PackageCheck}
              value={
                summary ? Number(summary.fulfilled_orders.current).toLocaleString("en-IN") : 0
              }
              subtext={pct(Number(summary?.fulfilled_orders.current ?? 0), totalOrders)}
              accent="emerald"
              href={ordersHref({ fulfillment_status: "fulfilled" })}
            />
            <StatTile
              label="Unfulfilled Orders"
              icon={PackageX}
              value={
                summary ? Number(summary.unfulfilled_orders.current).toLocaleString("en-IN") : 0
              }
              subtext={pct(Number(summary?.unfulfilled_orders.current ?? 0), totalOrders)}
              accent="amber"
              href={ordersHref({ fulfillment_status: "unfulfilled" })}
            />
            <StatTile
              label="Cancelled Orders"
              icon={Ban}
              value={cancelledCount.toLocaleString("en-IN")}
              subtext={pct(cancelledCount, totalOrders)}
              accent="slate"
              href={ordersHref({ status: "cancelled" })}
            />
          </section>

          {/* Root-cause fix: this page previously had stat cards only --
              no chart component was ever added when it was built. */}
          <section className="grid gap-4 lg:grid-cols-2">
            <StatusDonutCard
              title="COD vs Prepaid Orders"
              domain="payment"
              isLoading={breakdownsQuery.isLoading}
              centerLabel="Orders"
              data={breakdownsQuery.data?.payment_type}
              hrefFor={(status) => ordersHref({ payment_type: status })}
            />
            <SplitTimelineChart
              title="Orders Timeline"
              data={ordersTimelineData}
              seriesALabel="COD"
              seriesBLabel="Prepaid"
              seriesAColor="var(--warning)"
              seriesBColor="var(--info)"
              valueKind="count"
              isLoading={timeseriesQuery.isLoading}
            />
          </section>

          <DrilldownOrdersTable
            title="Orders in This Range"
            filters={dateParams}
            ordersHref={ordersHref({})}
          />
        </div>
      )}
    </>
  )
}

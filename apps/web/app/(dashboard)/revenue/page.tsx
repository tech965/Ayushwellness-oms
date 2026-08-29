"use client"

import * as React from "react"
import { IndianRupee, Wallet } from "lucide-react"
import { endOfDay, startOfDay, subDays } from "date-fns"

import { DrilldownOrdersTable } from "@/components/analytics/drilldown-orders-table"
import { MoneyDonutCard } from "@/components/analytics/money-donut"
import { SplitTimelineChart } from "@/components/analytics/split-timeline-chart"
import { PageHeader } from "@/components/shared/page-header"
import { StatTile } from "@/components/shared/stat-tile"
import { Skeleton } from "@/components/ui/skeleton"
import { formatDate, formatMoney } from "@/lib/format"
import { useUrlFilters } from "@/lib/use-url-filters"
import { useAnalyticsSummary, useRevenueTimeseries } from "@/services/analytics"

const FILTER_DEFAULTS = { date_from: "", date_to: "" }

function pct(part: number, total: number): string {
  if (total <= 0) return "0%"
  return `${((part / total) * 100).toFixed(1)}%`
}

function RevenueSkeleton() {
  return (
    <>
      <PageHeader title="Revenue Analytics" backHref="/dashboard" backLabel="Back to Dashboard" />
      <div className="flex flex-col gap-4">
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
          {Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="h-28 w-full" />
          ))}
        </div>
        <Skeleton className="h-[320px] w-full" />
      </div>
    </>
  )
}

export default function RevenueAnalyticsPage() {
  return (
    <React.Suspense fallback={<RevenueSkeleton />}>
      <RevenueAnalyticsContent />
    </React.Suspense>
  )
}

function RevenueAnalyticsContent() {
  const { filters } = useUrlFilters(FILTER_DEFAULTS)

  // Same default-window/resolution convention as every other drill-down
  // page (Dashboard, Order Breakdown) -- last 30 days when arriving
  // directly, exact carried-over range when arriving via a drill-down link.
  const resolvedFrom = filters.date_from
    ? new Date(filters.date_from)
    : startOfDay(subDays(new Date(), 29))
  const resolvedTo = filters.date_to ? new Date(filters.date_to) : endOfDay(new Date())
  const dateParams = { date_from: resolvedFrom.toISOString(), date_to: resolvedTo.toISOString() }
  const orderQueryString = new URLSearchParams(dateParams).toString()

  function drilldownHref(path: string, extra: Record<string, string> = {}): string {
    const params = new URLSearchParams(orderQueryString)
    for (const [key, value] of Object.entries(extra)) params.set(key, value)
    return `${path}?${params.toString()}`
  }

  const summaryQuery = useAnalyticsSummary(dateParams)
  const timeseriesQuery = useRevenueTimeseries({ ...dateParams, interval: "day" })
  const summary = summaryQuery.data
  const isLoading = summaryQuery.isLoading

  const totalRevenue = Number(summary?.total_revenue.current ?? 0)
  const codRevenue = Number(summary?.cod_value.current ?? 0)
  const prepaidRevenue = Number(summary?.prepaid_value.current ?? 0)

  const timelineData = (timeseriesQuery.data?.points ?? []).map((p) => ({
    bucket: p.bucket,
    seriesAValue: Number(p.cod_revenue),
    seriesBValue: Number(p.prepaid_revenue),
  }))

  return (
    <>
      <PageHeader
        title="Revenue Analytics"
        description={`${formatDate(resolvedFrom.toISOString())} – ${formatDate(resolvedTo.toISOString())}`}
        backHref="/dashboard"
        backLabel="Back to Dashboard"
      />

      {isLoading ? (
        <RevenueSkeleton />
      ) : (
        <div className="flex flex-col gap-6">
          <section className="grid grid-cols-1 gap-4 sm:grid-cols-3">
            <StatTile
              label="Total Revenue"
              icon={IndianRupee}
              value={formatMoney(totalRevenue)}
              subtext="COD + Prepaid, for the selected range"
              accent="emerald"
            />
            <StatTile
              label="COD Revenue"
              icon={Wallet}
              value={formatMoney(codRevenue)}
              subtext={pct(codRevenue, totalRevenue)}
              accent="amber"
              href={drilldownHref("/revenue/cod")}
            />
            <StatTile
              label="Prepaid Revenue"
              icon={IndianRupee}
              value={formatMoney(prepaidRevenue)}
              subtext={pct(prepaidRevenue, totalRevenue)}
              accent="blue"
              href={drilldownHref("/revenue/prepaid")}
            />
          </section>

          <section className="grid gap-4 lg:grid-cols-2">
            <MoneyDonutCard
              title="COD vs Prepaid Revenue"
              isLoading={isLoading}
              centerLabel="Revenue"
              segments={[
                {
                  key: "cod",
                  label: "COD",
                  value: codRevenue,
                  color: "var(--warning)",
                  href: drilldownHref("/revenue/cod"),
                },
                {
                  key: "prepaid",
                  label: "Prepaid",
                  value: prepaidRevenue,
                  color: "var(--info)",
                  href: drilldownHref("/revenue/prepaid"),
                },
              ]}
            />
            <SplitTimelineChart
              title="Revenue Timeline"
              data={timelineData}
              seriesALabel="COD"
              seriesBLabel="Prepaid"
              seriesAColor="var(--warning)"
              seriesBColor="var(--info)"
              valueKind="money"
              isLoading={timeseriesQuery.isLoading}
            />
          </section>

          <DrilldownOrdersTable
            title="Orders in This Range"
            filters={dateParams}
            ordersHref={drilldownHref("/orders")}
          />
        </div>
      )}
    </>
  )
}

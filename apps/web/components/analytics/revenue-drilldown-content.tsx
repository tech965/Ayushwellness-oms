"use client"

import * as React from "react"
import { CheckCircle2, Clock, IndianRupee } from "lucide-react"
import { endOfDay, startOfDay, subDays } from "date-fns"

import { DrilldownOrdersTable } from "@/components/analytics/drilldown-orders-table"
import { MoneyDonutCard } from "@/components/analytics/money-donut"
import { SplitTimelineChart } from "@/components/analytics/split-timeline-chart"
import { PageHeader } from "@/components/shared/page-header"
import { StatTile } from "@/components/shared/stat-tile"
import { Skeleton } from "@/components/ui/skeleton"
import { formatDate, formatMoney } from "@/lib/format"
import { useUrlFilters } from "@/lib/use-url-filters"
import { usePaymentStatusBreakdown, usePaymentStatusTimeseries } from "@/services/analytics"

const FILTER_DEFAULTS = { date_from: "", date_to: "" }

function pct(part: number, total: number): string {
  if (total <= 0) return "0%"
  return `${((part / total) * 100).toFixed(1)}%`
}

interface RevenueDrilldownContentProps {
  paymentType: "cod" | "prepaid"
  label: string
  accent: "amber" | "blue"
  seriesColor: string
}

/** Shared implementation for /revenue/cod and /revenue/prepaid -- same
 * Paid-vs-Pending analytics, differing only in which `payment_type` is
 * queried and the accent color, so the two page files stay thin wrappers
 * instead of duplicating this whole page.
 */
export function RevenueDrilldownContent({
  paymentType,
  label,
  accent,
  seriesColor,
}: RevenueDrilldownContentProps) {
  const { filters } = useUrlFilters(FILTER_DEFAULTS)

  const resolvedFrom = filters.date_from
    ? new Date(filters.date_from)
    : startOfDay(subDays(new Date(), 29))
  const resolvedTo = filters.date_to ? new Date(filters.date_to) : endOfDay(new Date())
  const dateParams = { date_from: resolvedFrom.toISOString(), date_to: resolvedTo.toISOString() }
  const orderQueryString = new URLSearchParams(dateParams).toString()

  function ordersHref(extra: Record<string, string> = {}): string {
    const params = new URLSearchParams(orderQueryString)
    params.set("payment_type", paymentType)
    for (const [key, value] of Object.entries(extra)) params.set(key, value)
    return `/orders?${params.toString()}`
  }

  // COD's "Pending" step drills into a Fulfilled/Unfulfilled breakdown
  // first (spec: Pending COD -> Fulfilled/Unfulfilled -> orders) rather
  // than straight to Orders -- Prepaid's Pending keeps the original
  // straight-to-Orders behavior unchanged, since that flow was never
  // part of this request.
  const pendingHref =
    paymentType === "cod"
      ? `/revenue/cod/pending?${orderQueryString}`
      : ordersHref({ payment_status: "pending" })

  const breakdownQuery = usePaymentStatusBreakdown({ ...dateParams, payment_type: paymentType })
  const timeseriesQuery = usePaymentStatusTimeseries({
    ...dateParams,
    interval: "day",
    payment_type: paymentType,
  })
  const breakdown = breakdownQuery.data
  const isLoading = breakdownQuery.isLoading

  const total = Number(breakdown?.total_revenue ?? 0)
  const paid = Number(breakdown?.paid_revenue ?? 0)
  const pending = Number(breakdown?.pending_revenue ?? 0)

  const timelineData = (timeseriesQuery.data?.points ?? []).map((p) => ({
    bucket: p.bucket,
    seriesAValue: Number(p.paid_revenue),
    seriesBValue: Number(p.pending_revenue),
  }))

  return (
    <>
      <PageHeader
        title={`${label} Revenue`}
        description={`${formatDate(resolvedFrom.toISOString())} – ${formatDate(resolvedTo.toISOString())}`}
        backHref={`/revenue?${orderQueryString}`}
        backLabel="Back to Revenue Analytics"
      />

      {isLoading ? (
        <div className="flex flex-col gap-4">
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
            {Array.from({ length: 3 }).map((_, i) => (
              <Skeleton key={i} className="h-28 w-full" />
            ))}
          </div>
          <Skeleton className="h-[320px] w-full" />
        </div>
      ) : (
        <div className="flex flex-col gap-6">
          <section className="grid grid-cols-1 gap-4 sm:grid-cols-3">
            <StatTile
              label={`Total ${label}`}
              icon={IndianRupee}
              value={formatMoney(total)}
              accent={accent}
              href={ordersHref()}
            />
            <StatTile
              label={`Paid ${label}`}
              icon={CheckCircle2}
              value={formatMoney(paid)}
              subtext={pct(paid, total)}
              accent="emerald"
              href={ordersHref({ payment_status: "paid" })}
            />
            <StatTile
              label={`Pending ${label}`}
              icon={Clock}
              value={formatMoney(pending)}
              subtext={pct(pending, total)}
              accent="orange"
              href={pendingHref}
            />
          </section>

          <section className="grid gap-4 lg:grid-cols-2">
            <MoneyDonutCard
              title={`Paid vs Pending ${label}`}
              isLoading={isLoading}
              centerLabel={label}
              segments={[
                {
                  key: "paid",
                  label: "Paid",
                  value: paid,
                  color: "var(--success)",
                  href: ordersHref({ payment_status: "paid" }),
                },
                {
                  key: "pending",
                  label: "Pending",
                  value: pending,
                  color: "var(--muted-foreground)",
                  href: pendingHref,
                },
              ]}
            />
            <SplitTimelineChart
              title={`${label} Revenue Timeline`}
              data={timelineData}
              seriesALabel="Paid"
              seriesBLabel="Pending"
              seriesAColor="var(--success)"
              seriesBColor={seriesColor}
              valueKind="money"
              isLoading={timeseriesQuery.isLoading}
            />
          </section>

          <DrilldownOrdersTable
            title={`${label} Orders`}
            filters={{ ...dateParams, payment_type: paymentType }}
            ordersHref={ordersHref()}
          />
        </div>
      )}
    </>
  )
}

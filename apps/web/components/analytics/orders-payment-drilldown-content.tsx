"use client"

import * as React from "react"
import { CheckCircle2, Clock, ShoppingCart } from "lucide-react"
import { endOfDay, startOfDay, subDays } from "date-fns"

import { DrilldownOrdersTable } from "@/components/analytics/drilldown-orders-table"
import { SplitTimelineChart } from "@/components/analytics/split-timeline-chart"
import { StatusDonutCard } from "@/components/dashboard/status-donut-card"
import { PageHeader } from "@/components/shared/page-header"
import { StatTile } from "@/components/shared/stat-tile"
import { Skeleton } from "@/components/ui/skeleton"
import { formatDate } from "@/lib/format"
import { useUrlFilters } from "@/lib/use-url-filters"
import { usePaymentStatusBreakdown, usePaymentStatusTimeseries } from "@/services/analytics"

const FILTER_DEFAULTS = { date_from: "", date_to: "" }

function pct(part: number, total: number): string {
  if (total <= 0) return "0%"
  return `${((part / total) * 100).toFixed(1)}%`
}

interface OrdersPaymentDrilldownContentProps {
  paymentType: "cod" | "prepaid"
  label: string
  accent: "amber" | "blue"
  seriesColor: string
}

/** Shared implementation for /orders/breakdown/cod and .../prepaid --
 * order-COUNT analytics (as opposed to `RevenueDrilldownContent`'s
 * revenue figures), reusing the same `payment-status-breakdown`/
 * `payment-status-timeseries` endpoints' count fields.
 */
export function OrdersPaymentDrilldownContent({
  paymentType,
  label,
  accent,
  seriesColor,
}: OrdersPaymentDrilldownContentProps) {
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

  const breakdownQuery = usePaymentStatusBreakdown({ ...dateParams, payment_type: paymentType })
  const timeseriesQuery = usePaymentStatusTimeseries({
    ...dateParams,
    interval: "day",
    payment_type: paymentType,
  })
  const breakdown = breakdownQuery.data
  const isLoading = breakdownQuery.isLoading

  const total = breakdown?.total_count ?? 0
  const paid = breakdown?.paid_count ?? 0
  const pending = breakdown?.pending_count ?? 0

  const timelineData = (timeseriesQuery.data?.points ?? []).map((p) => ({
    bucket: p.bucket,
    seriesAValue: p.paid_orders,
    seriesBValue: p.pending_orders,
  }))

  return (
    <>
      <PageHeader
        title={`${label} Orders`}
        description={`${formatDate(resolvedFrom.toISOString())} – ${formatDate(resolvedTo.toISOString())}`}
        backHref={`/orders/breakdown?${orderQueryString}`}
        backLabel="Back to Order Breakdown"
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
              label={`Total ${label} Orders`}
              icon={ShoppingCart}
              value={total.toLocaleString("en-IN")}
              accent={accent}
              href={ordersHref()}
            />
            <StatTile
              label={`Paid ${label} Orders`}
              icon={CheckCircle2}
              value={paid.toLocaleString("en-IN")}
              subtext={pct(paid, total)}
              accent="emerald"
              href={ordersHref({ payment_status: "paid" })}
            />
            <StatTile
              label={`Pending ${label} Orders`}
              icon={Clock}
              value={pending.toLocaleString("en-IN")}
              subtext={pct(pending, total)}
              accent="orange"
              href={ordersHref({ payment_status: "pending" })}
            />
          </section>

          <section className="grid gap-4 lg:grid-cols-2">
            <StatusDonutCard
              title={`Paid vs Pending ${label} Orders`}
              domain="payment"
              isLoading={isLoading}
              centerLabel="Orders"
              data={[
                { status: "paid", count: paid },
                { status: "pending", count: pending },
              ]}
              hrefFor={(status) => ordersHref({ payment_status: status })}
            />
            <SplitTimelineChart
              title={`${label} Orders Timeline`}
              data={timelineData}
              seriesALabel="Paid"
              seriesBLabel="Pending"
              seriesAColor="var(--success)"
              seriesBColor={seriesColor}
              valueKind="count"
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

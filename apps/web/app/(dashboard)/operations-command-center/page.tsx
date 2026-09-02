"use client"

import * as React from "react"
import { AlertTriangle, IndianRupee, ShoppingCart, TrendingUp } from "lucide-react"

import { AttentionSection } from "@/components/operations-command-center/attention-section"
import { BusinessOpportunitiesSection } from "@/components/operations-command-center/business-opportunities-section"
import { InsightsPanel } from "@/components/operations-command-center/insights-panel"
import { OperationsHealthSection } from "@/components/operations-command-center/operations-health-section"
import { DateRangePicker, type DateRangeValue } from "@/components/shared/date-range-picker"
import { PageHeader } from "@/components/shared/page-header"
import { StatTile } from "@/components/shared/stat-tile"
import { Skeleton } from "@/components/ui/skeleton"
import { formatMoney } from "@/lib/format"
import { istEndOfDay, istStartOfDay } from "@/lib/ist-date"
import { useUrlFilters } from "@/lib/use-url-filters"
import { useOperationsCommandCenter } from "@/services/operations-command-center"

const FILTER_DEFAULTS = { date_from: "", date_to: "" }

export default function OperationsCommandCenterPage() {
  return (
    <React.Suspense fallback={<Skeleton className="h-[600px] w-full" />}>
      <OperationsCommandCenterContent />
    </React.Suspense>
  )
}

function OperationsCommandCenterContent() {
  const { filters, setFilters } = useUrlFilters(FILTER_DEFAULTS)

  const now = new Date()
  const resolvedFrom = filters.date_from
    ? new Date(filters.date_from)
    : istStartOfDay(new Date(now.getTime() - 29 * 24 * 60 * 60 * 1000))
  const resolvedTo = filters.date_to ? new Date(filters.date_to) : istEndOfDay(now)
  const displayRange: DateRangeValue = { from: resolvedFrom, to: resolvedTo }

  const params = {
    date_from: resolvedFrom.toISOString(),
    date_to: resolvedTo.toISOString(),
  }

  const query = useOperationsCommandCenter(params)
  const data = query.data

  return (
    <>
      <PageHeader
        title="🤖 Operations Command Center"
        description="A real-time view of what needs attention, what is performing well, and where the business has opportunities."
        actions={
          <DateRangePicker
            value={displayRange}
            onChange={(range) =>
              setFilters({
                date_from: range.from ? range.from.toISOString() : "",
                date_to: range.to ? range.to.toISOString() : "",
              })
            }
          />
        }
      />

      {query.isLoading ? (
        <Skeleton className="h-[600px] w-full" />
      ) : query.isError || !data ? (
        <p className="text-muted-foreground text-sm">
          Could not load Operations Command Center data. Try again later.
        </p>
      ) : (
        <div className="flex flex-col gap-6">
          <section className="grid grid-cols-2 gap-4 lg:grid-cols-4">
            <StatTile
              label="Total Orders"
              value={data.summary.total_orders.toLocaleString("en-IN")}
              icon={ShoppingCart}
              accent="emerald"
            />
            <StatTile
              label="Revenue"
              value={formatMoney(data.summary.total_revenue)}
              icon={IndianRupee}
              accent="emerald"
            />
            <StatTile
              label="Requires Attention"
              value={data.summary.requires_attention_count.toLocaleString("en-IN")}
              icon={AlertTriangle}
              accent="orange"
            />
            <StatTile
              label="Growth"
              value={
                data.summary.orders_growth_pct === null
                  ? "—"
                  : `${data.summary.orders_growth_pct > 0 ? "+" : ""}${data.summary.orders_growth_pct.toFixed(1)}%`
              }
              icon={TrendingUp}
              accent="blue"
            />
          </section>

          <AttentionSection items={data.attention_items} />
          <OperationsHealthSection health={data.operations_health} />
          <BusinessOpportunitiesSection opportunities={data.business_opportunities} />
          <InsightsPanel insights={data.insights} />
        </div>
      )}
    </>
  )
}

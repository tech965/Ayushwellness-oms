"use client"

import * as React from "react"
import { Boxes, Download, IndianRupee, MapPin, ShoppingCart } from "lucide-react"

import { MarketIntelligence } from "@/components/supply-intelligence/market-intelligence"
import { StateDetailPanel } from "@/components/supply-intelligence/state-detail-panel"
import { StateGridMap } from "@/components/supply-intelligence/state-grid-map"
import { StateLeaderboard } from "@/components/supply-intelligence/state-leaderboard"
import { SupplyRecommendations } from "@/components/supply-intelligence/supply-recommendations"
import { DateRangePicker, type DateRangeValue } from "@/components/shared/date-range-picker"
import { PageHeader } from "@/components/shared/page-header"
import { StatTile } from "@/components/shared/stat-tile"
import { Button } from "@/components/ui/button"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import { formatMoney } from "@/lib/format"
import { istEndOfDay, istStartOfDay } from "@/lib/ist-date"
import { useUrlFilters } from "@/lib/use-url-filters"
import { useSupplyIntelligence } from "@/services/supply-intelligence"
import type { MapMetric } from "@/types/supply-intelligence"

const FILTER_DEFAULTS = { date_from: "", date_to: "", state: "" }

const METRIC_OPTIONS: { value: MapMetric; label: string }[] = [
  { value: "orders", label: "Orders" },
  { value: "revenue", label: "Revenue" },
  { value: "customers", label: "Customers" },
  { value: "rto_rate_pct", label: "RTO Rate" },
]

/** Builds and downloads a CSV client-side from data the page already
 * has in memory -- deliberately not calling the existing `/orders/export`
 * (xlsx) endpoint or touching `export_service.py`; this reuses the same
 * Blob + hidden-anchor download MECHANISM `services/orders.ts` already
 * uses, just with no extra network round-trip for data that's already
 * loaded.
 */
function downloadStatesCsv(states: { state: string; orders: number; revenue: string; customers: number; delivered: number; rto: number; rto_rate_pct: number | null; growth_pct: number | null }[]) {
  const header = ["State", "Orders", "Revenue", "Customers", "Delivered", "RTO", "RTO Rate %", "Growth %"]
  const rows = states
    .filter((s) => s.orders > 0)
    .map((s) => [
      s.state,
      s.orders,
      s.revenue,
      s.customers,
      s.delivered,
      s.rto,
      s.rto_rate_pct === null ? "" : s.rto_rate_pct.toFixed(1),
      s.growth_pct === null ? "" : s.growth_pct.toFixed(1),
    ])
  const csv = [header, ...rows]
    .map((row) => row.map((cell) => `"${String(cell).replace(/"/g, '""')}"`).join(","))
    .join("\n")
  const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" })
  const url = window.URL.createObjectURL(blob)
  const link = document.createElement("a")
  link.href = url
  link.download = "supply-intelligence-states.csv"
  document.body.appendChild(link)
  link.click()
  link.remove()
  window.URL.revokeObjectURL(url)
}

export default function SupplyIntelligencePage() {
  return (
    <React.Suspense fallback={<Skeleton className="h-[600px] w-full" />}>
      <SupplyIntelligenceContent />
    </React.Suspense>
  )
}

function SupplyIntelligenceContent() {
  const { filters, setFilters } = useUrlFilters(FILTER_DEFAULTS)
  const [metric, setMetric] = React.useState<MapMetric>("orders")

  const now = new Date()
  const resolvedFrom = filters.date_from
    ? new Date(filters.date_from)
    : istStartOfDay(new Date(now.getTime() - 29 * 24 * 60 * 60 * 1000))
  const resolvedTo = filters.date_to ? new Date(filters.date_to) : istEndOfDay(now)
  const displayRange: DateRangeValue = { from: resolvedFrom, to: resolvedTo }

  const params = {
    date_from: resolvedFrom.toISOString(),
    date_to: resolvedTo.toISOString(),
    state: filters.state || undefined,
  }

  const query = useSupplyIntelligence(params)
  const data = query.data

  function selectState(state: string) {
    setFilters({ state: state === filters.state ? "" : state })
  }

  return (
    <>
      <PageHeader
        title="🇮🇳 India Supply Intelligence"
        description="India-wide demand & distribution overview."
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <DateRangePicker
              value={displayRange}
              onChange={(range) =>
                setFilters({
                  date_from: range.from ? range.from.toISOString() : "",
                  date_to: range.to ? range.to.toISOString() : "",
                })
              }
            />
            <Button
              variant="outline"
              size="sm"
              disabled={!data?.states.length}
              onClick={() => data && downloadStatesCsv(data.states)}
            >
              <Download className="size-3.5" />
              Export State Analytics
            </Button>
          </div>
        }
      />

      {query.isLoading ? (
        <Skeleton className="h-[600px] w-full" />
      ) : query.isError || !data ? (
        <p className="text-muted-foreground text-sm">
          Could not load supply intelligence data. Try again later.
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
              label="Total Revenue"
              value={formatMoney(data.summary.total_revenue)}
              icon={IndianRupee}
              accent="emerald"
            />
            <StatTile
              label="Active States"
              value={data.summary.active_states.toLocaleString("en-IN")}
              icon={MapPin}
              accent="blue"
            />
            <StatTile
              label="Top State"
              value={data.summary.top_state ?? "—"}
              subtext={
                data.summary.top_revenue_state && data.summary.top_revenue_state !== data.summary.top_state
                  ? `Top revenue: ${data.summary.top_revenue_state}`
                  : undefined
              }
              icon={Boxes}
              accent="violet"
            />
          </section>

          {data.unmapped_order_count > 0 ? (
            <p className="text-muted-foreground text-xs">
              {data.unmapped_order_count.toLocaleString("en-IN")} order(s) in this period have no
              recognized state and are excluded from the figures below.
            </p>
          ) : null}

          <div className="flex items-center gap-2">
            <span className="text-muted-foreground text-sm">Map metric:</span>
            <Select value={metric} onValueChange={(v) => setMetric(v as MapMetric)}>
              <SelectTrigger className="w-[150px]" size="sm">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {METRIC_OPTIONS.map((option) => (
                  <SelectItem key={option.value} value={option.value}>
                    {option.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="grid gap-4 xl:grid-cols-2">
            <StateGridMap
              states={data.states}
              metric={metric}
              selectedState={filters.state || null}
              onSelectState={selectState}
            />
            <StateLeaderboard
              states={data.states}
              selectedState={filters.state || null}
              onSelectState={selectState}
            />
          </div>

          <MarketIntelligence insights={data.insights} />

          {filters.state ? (
            <StateDetailPanel
              state={filters.state}
              detail={data.selected_state ?? undefined}
              isLoading={query.isFetching && !data.selected_state}
            />
          ) : null}

          <SupplyRecommendations insights={data.insights} />
        </div>
      )}
    </>
  )
}

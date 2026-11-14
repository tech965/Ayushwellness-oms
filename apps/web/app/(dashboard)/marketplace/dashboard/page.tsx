"use client"

import * as React from "react"
import {
  CheckCircle2,
  Clock3,
  IndianRupee,
  PackageCheck,
  PackageX,
  ShoppingCart,
  Store,
  Truck,
  Wallet,
  XCircle,
} from "lucide-react"

import { KpiCard } from "@/components/dashboard/kpi-card"
import { DateRangePicker, type DateRangeValue } from "@/components/shared/date-range-picker"
import { PageHeader } from "@/components/shared/page-header"
import { Skeleton } from "@/components/ui/skeleton"
import { formatMoney } from "@/lib/format"
import { useUrlFilters } from "@/lib/use-url-filters"
import { useMarketplaceDashboardSummary } from "@/services/marketplace"

const FILTER_DEFAULTS = { date_from: "", date_to: "" }

function money(value: string): string {
  return formatMoney(value)
}
function count(value: string): string {
  return Number(value).toLocaleString("en-IN")
}

function MarketplaceDashboardSkeleton() {
  return (
    <>
      <PageHeader
        title="Marketplace Dashboard"
        description="Amazon, Flipkart, and Meesho orders at a glance."
      />
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        {Array.from({ length: 8 }).map((_, i) => (
          <Skeleton key={i} className="h-24 w-full" />
        ))}
      </div>
    </>
  )
}

export default function MarketplaceDashboardPage() {
  return (
    <React.Suspense fallback={<MarketplaceDashboardSkeleton />}>
      <MarketplaceDashboardContent />
    </React.Suspense>
  )
}

function MarketplaceDashboardContent() {
  const { filters, setFilters } = useUrlFilters(FILTER_DEFAULTS)

  const dateRange: DateRangeValue = {
    from: filters.date_from ? new Date(filters.date_from) : undefined,
    to: filters.date_to ? new Date(filters.date_to) : undefined,
  }

  const summaryQuery = useMarketplaceDashboardSummary({
    date_from: filters.date_from || undefined,
    date_to: filters.date_to || undefined,
  })

  if (summaryQuery.isLoading) return <MarketplaceDashboardSkeleton />

  const data = summaryQuery.data

  return (
    <>
      <PageHeader
        title="Marketplace Dashboard"
        description="Amazon, Flipkart, and Meesho orders at a glance — kept separate from the main Shopify dashboard."
        actions={
          <DateRangePicker
            value={dateRange}
            onChange={(range) =>
              setFilters({
                date_from: range.from ? range.from.toISOString() : "",
                date_to: range.to ? range.to.toISOString() : "",
              })
            }
          />
        }
      />
      <div className="flex flex-col gap-6">
        <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
          <KpiCard
            label="Total Marketplace Orders"
            icon={ShoppingCart}
            kpi={data?.total_orders}
            format={count}
            href="/marketplace/orders"
            accent="blue"
          />
          <KpiCard
            label="Amazon Orders"
            icon={Store}
            kpi={data?.amazon_orders}
            format={count}
            href="/marketplace/orders/amazon"
            accent="amber"
          />
          <KpiCard
            label="Flipkart Orders"
            icon={Store}
            kpi={data?.flipkart_orders}
            format={count}
            href="/marketplace/orders/flipkart"
            accent="violet"
          />
          <KpiCard
            label="Meesho Orders"
            icon={Store}
            kpi={data?.meesho_orders}
            format={count}
            href="/marketplace/orders/meesho"
            accent="emerald"
          />
          <KpiCard
            label="Total Marketplace Sales"
            icon={IndianRupee}
            kpi={data?.total_sales}
            format={money}
            accent="emerald"
          />
          <KpiCard
            label="Pending Orders"
            icon={Clock3}
            kpi={data?.pending_orders}
            format={count}
            accent="amber"
            invert
          />
          <KpiCard
            label="Unfulfilled Orders"
            icon={PackageX}
            kpi={data?.unfulfilled_orders}
            format={count}
            accent="orange"
            invert
          />
          <KpiCard
            label="Shipped Orders"
            icon={Truck}
            kpi={data?.shipped_orders}
            format={count}
            accent="blue"
          />
          <KpiCard
            label="Delivered Orders"
            icon={PackageCheck}
            kpi={data?.delivered_orders}
            format={count}
            accent="emerald"
          />
          <KpiCard
            label="Cancelled Orders"
            icon={XCircle}
            kpi={data?.cancelled_orders}
            format={count}
            accent="slate"
            invert
          />
          <KpiCard
            label="COD Orders"
            icon={Wallet}
            kpi={data?.cod_orders}
            format={count}
            accent="amber"
          />
          <KpiCard
            label="Prepaid Orders"
            icon={CheckCircle2}
            kpi={data?.prepaid_orders}
            format={count}
            accent="blue"
          />
        </div>
      </div>
    </>
  )
}

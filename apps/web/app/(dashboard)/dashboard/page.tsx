"use client"

import * as React from "react"
import {
  AlertTriangle,
  Boxes,
  Clock3,
  IndianRupee,
  PackageCheck,
  PackageX,
  RefreshCcw,
  RotateCcw,
  ShoppingCart,
  Truck,
  Undo2,
  Users,
  Wallet,
} from "lucide-react"
import { endOfDay, startOfDay, subDays } from "date-fns"

import { CourierPerformanceCard } from "@/components/dashboard/courier-performance-card"
import { KpiCard } from "@/components/dashboard/kpi-card"
import { BreakdownList } from "@/components/dashboard/breakdown-list"
import { OrdersRevenueChart } from "@/components/dashboard/orders-revenue-chart"
import { PaymentBreakdownCard } from "@/components/dashboard/payment-breakdown-card"
import { RecentActivityCard } from "@/components/dashboard/recent-activity-card"
import {
  ShipmentOverviewStrip,
  type ShipmentOverviewItem,
} from "@/components/dashboard/shipment-overview-strip"
import { StatusDonutCard } from "@/components/dashboard/status-donut-card"
import { TopProductsCard } from "@/components/dashboard/top-products-card"
import {
  DateRangePicker,
  type DateRangeValue,
} from "@/components/shared/date-range-picker"
import { PageHeader } from "@/components/shared/page-header"
import { Skeleton } from "@/components/ui/skeleton"
import { formatMoney } from "@/lib/format"
import { useUrlFilters } from "@/lib/use-url-filters"
import {
  useAnalyticsSummary,
  useBreakdowns,
  useCourierPerformance,
  useOrdersTimeseries,
  useRecentActivity,
  useTopProducts,
} from "@/services/analytics"
import type { TimeseriesInterval } from "@/types/analytics"

const FILTER_DEFAULTS = { date_from: "", date_to: "" }

function money(value: string): string {
  return formatMoney(value)
}
function count(value: string): string {
  return Number(value).toLocaleString("en-IN")
}

function DashboardSkeleton() {
  return (
    <>
      <PageHeader
        title="Dashboard"
        description="Live operational overview of your OMS."
      />
      <div className="flex flex-col gap-6">
        <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-24 w-full" />
          ))}
        </div>
        <Skeleton className="h-[300px] w-full" />
      </div>
    </>
  )
}

export default function DashboardPage() {
  return (
    <React.Suspense fallback={<DashboardSkeleton />}>
      <DashboardContent />
    </React.Suspense>
  )
}

function DashboardContent() {
  const { filters, setFilters } = useUrlFilters(FILTER_DEFAULTS)
  const [interval, setInterval] = React.useState<TimeseriesInterval>("day")

  // Resolved to concrete Dates (never left undefined) so every KPI/chart
  // request AND every drill-down link below is built from the exact same
  // range the picker displays — including when no filter has been picked
  // yet. Leaving date_from/date_to unset here used to mean "let the
  // backend default to its own last-30-days-from-this-instant window,"
  // which the Orders page's *own* filters knew nothing about: a
  // drill-down link with no date params landed on Orders with no date
  // filter at all (i.e. all-time), silently mismatching whatever count
  // the dashboard had just shown — the "Dashboard says 4, Orders shows 7"
  // class of bug. `endOfDay` (not `new Date()`) as the default upper
  // bound also matches the Orders page's own "Last 30 Days" preset
  // (`components/shared/date-range-picker.tsx`'s `endOfToday()`), so the
  // two pages agree even when a user separately re-picks the same preset
  // on Orders instead of following a drill-down link.
  const resolvedFrom = filters.date_from
    ? new Date(filters.date_from)
    : startOfDay(subDays(new Date(), 29))
  const resolvedTo = filters.date_to ? new Date(filters.date_to) : endOfDay(new Date())

  const displayRange: DateRangeValue = { from: resolvedFrom, to: resolvedTo }

  const dateParams = {
    date_from: resolvedFrom.toISOString(),
    date_to: resolvedTo.toISOString(),
  }

  const summaryQuery = useAnalyticsSummary(dateParams)
  const timeseriesQuery = useOrdersTimeseries({ ...dateParams, interval })
  const breakdownsQuery = useBreakdowns(dateParams)
  const topProductsQuery = useTopProducts({ ...dateParams, limit: 10 })
  const courierQuery = useCourierPerformance(dateParams)
  const recentActivityQuery = useRecentActivity()

  const orderQueryString = new URLSearchParams(dateParams).toString()

  function ordersHref(extra: Record<string, string>): string {
    const params = new URLSearchParams(orderQueryString)
    for (const [key, value] of Object.entries(extra)) params.set(key, value)
    return `/orders?${params.toString()}`
  }

  const summary = summaryQuery.data

  const shipmentOverviewItems: ShipmentOverviewItem[] = [
    {
      key: "delivered",
      label: "Delivered",
      icon: PackageCheck,
      kpi: summary?.delivered_shipments,
      tone: "success",
      href: ordersHref({ shipment_status: "delivered" }),
    },
    {
      key: "in_transit",
      label: "In Transit",
      icon: Truck,
      kpi: summary?.in_transit_shipments,
      tone: "info",
      href: ordersHref({ shipment_status: "in_transit" }),
    },
    {
      key: "out_for_delivery",
      label: "Out for Delivery",
      icon: Truck,
      kpi: summary?.out_for_delivery_shipments,
      tone: "purple",
      href: ordersHref({ shipment_status: "out_for_delivery" }),
    },
    {
      key: "delayed",
      label: "Delayed",
      icon: AlertTriangle,
      kpi: summary?.delayed_shipments,
      tone: "warning",
      href: "/shipments",
    },
    {
      key: "ndr",
      label: "NDR",
      icon: Clock3,
      kpi: summary?.open_ndr,
      tone: "orange",
      href: "/ndr",
    },
    {
      key: "rto",
      label: "RTO",
      icon: RotateCcw,
      kpi: summary?.open_rto,
      tone: "danger",
      href: "/rto",
    },
  ]

  return (
    <>
      <PageHeader
        title="Dashboard"
        description="Live operational overview of your OMS."
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

      <div className="flex flex-col gap-6">
        <section className="grid grid-cols-2 gap-4 lg:grid-cols-4">
          <KpiCard
            label="Total Orders"
            icon={ShoppingCart}
            kpi={summary?.total_orders}
            format={count}
            href={ordersHref({})}
            accent="blue"
          />
          <KpiCard
            label="Total Revenue"
            icon={IndianRupee}
            kpi={summary?.total_revenue}
            format={money}
            href={ordersHref({})}
            accent="emerald"
          />
          <KpiCard
            label="Total Customers"
            icon={Users}
            kpi={summary?.total_customers}
            format={count}
            href="/customers"
            accent="violet"
          />
          <KpiCard
            label="Total Products"
            icon={Boxes}
            kpi={summary?.total_products}
            format={count}
            href="/products"
            accent="amber"
          />
        </section>

        <section className="grid grid-cols-2 gap-4 lg:grid-cols-3 xl:grid-cols-6">
          <KpiCard
            label="Fulfilled Orders"
            icon={PackageCheck}
            kpi={summary?.fulfilled_orders}
            format={count}
            href={ordersHref({ fulfillment_status: "fulfilled" })}
            accent="emerald"
          />
          <KpiCard
            label="Unfulfilled Orders"
            icon={PackageX}
            kpi={summary?.unfulfilled_orders}
            format={count}
            href={ordersHref({ fulfillment_status: "unfulfilled" })}
            accent="amber"
            invert
          />
          <KpiCard
            label="COD Orders"
            icon={Wallet}
            kpi={summary?.cod_orders}
            format={count}
            href={ordersHref({ payment_type: "cod" })}
            accent="amber"
          />
          <KpiCard
            label="Prepaid Orders"
            icon={Wallet}
            kpi={summary?.prepaid_orders}
            format={count}
            href={ordersHref({ payment_type: "prepaid" })}
            accent="blue"
          />
          <KpiCard
            label="Returns"
            icon={Undo2}
            kpi={summary?.returns}
            format={count}
            href="/returns"
            accent="slate"
          />
          <KpiCard
            label="Refunds"
            icon={RefreshCcw}
            kpi={summary?.refunds}
            format={count}
            href="/refunds"
            accent="slate"
          />
        </section>

        <OrdersRevenueChart
          data={timeseriesQuery.data}
          interval={interval}
          onIntervalChange={setInterval}
          isLoading={timeseriesQuery.isLoading}
        />

        <section className="grid gap-4 lg:grid-cols-2">
          <StatusDonutCard
            title="Fulfillment Status"
            domain="fulfillment"
            data={breakdownsQuery.data?.fulfillment_status}
            isLoading={breakdownsQuery.isLoading}
            hrefFor={(status) => ordersHref({ fulfillment_status: status })}
            centerLabel="Total Orders"
          />
          <PaymentBreakdownCard
            paymentType={breakdownsQuery.data?.payment_type}
            paymentStatus={breakdownsQuery.data?.payment_status}
            isLoading={breakdownsQuery.isLoading}
            hrefForType={(status) => ordersHref({ payment_type: status })}
            hrefForStatus={(status) => ordersHref({ payment_status: status })}
          />
        </section>

        <ShipmentOverviewStrip
          items={shipmentOverviewItems}
          isLoading={summaryQuery.isLoading}
        />

        <section className="grid gap-4 lg:grid-cols-2">
          <BreakdownList
            title="Order Status"
            domain="order"
            data={breakdownsQuery.data?.order_status}
            isLoading={breakdownsQuery.isLoading}
            hrefFor={(status) => ordersHref({ status })}
          />
          <BreakdownList
            title="Shipment Pipeline"
            domain="shipment"
            data={breakdownsQuery.data?.shipment_status}
            isLoading={breakdownsQuery.isLoading}
            hrefFor={(status) => ordersHref({ shipment_status: status })}
          />
        </section>

        <section className="grid gap-4 lg:grid-cols-2">
          <TopProductsCard
            data={topProductsQuery.data}
            isLoading={topProductsQuery.isLoading}
          />
          <CourierPerformanceCard
            data={courierQuery.data}
            isLoading={courierQuery.isLoading}
          />
        </section>

        <RecentActivityCard
          data={recentActivityQuery.data}
          isLoading={recentActivityQuery.isLoading}
        />
      </div>
    </>
  )
}

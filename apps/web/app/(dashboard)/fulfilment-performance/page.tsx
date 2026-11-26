"use client"

import * as React from "react"
import { format, parseISO } from "date-fns"

import { ClickableBarChart, type ChartDatum } from "@/components/shared/clickable-charts"
import type { DataTableColumn } from "@/components/shared/data-table"
import { FilterBar } from "@/components/shared/filter-bar"
import { OrderDrilldown, useDebouncedValue } from "@/components/shared/order-drilldown"
import { PageHeader } from "@/components/shared/page-header"
import { QueryStates } from "@/components/shared/query-states"
import { SelectableStatCard } from "@/components/shared/selectable-stat-card"
import { ServiceNames } from "@/components/shared/service-names"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { formatDate } from "@/lib/format"
import { formatShare } from "@/lib/percent"
import { formatDays, paymentLabel } from "@/lib/shipment-labels"
import { useDateRangeFilter } from "@/lib/use-date-range-filter"
import { usePaginationState } from "@/lib/use-pagination"
import { cn } from "@/lib/utils"
import { useCouriers } from "@/services/couriers"
import {
  useDeliveryPerformance,
  useDeliveryPerformanceHistory,
  useFulfilmentOrders,
  useFulfilmentPerformance,
  useFulfilmentPerformanceHistory,
  useShipmentStatusAnalytics,
} from "@/services/shipments"
import type { FulfilmentScope, PerformanceOrderRow } from "@/types/shipment"

const ALL_COURIERS = "__all__"

interface Selection {
  scope: FulfilmentScope
  day?: string
}

const SCOPE_TITLES: Record<FulfilmentScope, string> = {
  fulfilled: "Fulfilled orders (used for Average Fulfilment Time)",
  delivered: "Delivered orders (used for Average Delivery Time)",
  cod_delivered: "Delivered COD orders",
  prepaid_delivered: "Delivered prepaid orders",
  pending: "Orders awaiting fulfilment",
  pending_24h: "Orders pending for more than 24 hours",
  pending_48h: "Orders pending for more than 48 hours",
}

function shortDay(iso: string): string {
  return format(parseISO(iso), "d MMM")
}

/** "Based on 5,144 orders (54.2%)": the order count plus its share of every
 * order in the same filtered period. The share is omitted (never shown as 0%)
 * while the total is unknown or zero.
 */
function coverage(count: number, total: number | undefined): string {
  const share = formatShare(count, total)
  const base = `Based on ${plural(count, "order", "orders")}`
  return share === "—" ? base : `${base} (${share})`
}

/** "11.7% of all 9,493 orders" for the pending-work cards. */
function ofAll(count: number, total: number | undefined): string | undefined {
  const share = formatShare(count, total)
  return share === "—" || total === undefined
    ? undefined
    : `${share} of all ${total.toLocaleString("en-IN")} orders`
}

function plural(count: number, one: string, many: string): string {
  return `${count.toLocaleString("en-IN")} ${count === 1 ? one : many}`
}

function durationColumn(scope: FulfilmentScope): DataTableColumn<PerformanceOrderRow> {
  if (scope === "fulfilled") {
    return {
      id: "duration",
      header: "Time to fulfil",
      className: "text-right",
      cell: (row) => formatDays(row.fulfilment_days),
    }
  }
  if (scope.startsWith("pending")) {
    return {
      id: "duration",
      header: "Waiting for",
      className: "text-right",
      cell: (row) =>
        row.pending_hours === null ? "—" : `${Math.round(row.pending_hours)} hours`,
    }
  }
  return {
    id: "duration",
    header: "Delivery time",
    className: "text-right",
    cell: (row) => formatDays(row.delivery_days),
  }
}

function orderColumns(scope: FulfilmentScope): DataTableColumn<PerformanceOrderRow>[] {
  return [
    {
      id: "order",
      header: "Order",
      cell: (row) => (
        <span className="font-medium">
          {row.order_number}
          <span className="text-muted-foreground block text-xs font-normal">
            {formatDate(row.order_datetime)}
          </span>
        </span>
      ),
    },
    { id: "payment", header: "Payment", cell: (row) => paymentLabel(row.payment_type) },
    { id: "courier", header: "Courier", cell: (row) => row.courier_name ?? "—" },
    {
      id: "pickup",
      header: "Pickup date",
      cell: (row) => (row.pickup_date ? formatDate(row.pickup_date) : "Not picked up"),
    },
    {
      id: "delivery",
      header: "Delivery date",
      cell: (row) =>
        row.actual_delivery_date ? formatDate(row.actual_delivery_date) : "—",
    },
    durationColumn(scope),
  ]
}

/** OMS SOW Module 3 — Fulfilment & Delivery Performance, laid out for
 * management. Averages and the pending-work counters are kept apart on
 * purpose (a pending order has no pickup yet, so it can never be part of an
 * average); every card and chart bar opens the exact orders behind it.
 */
function FulfilmentPerformancePageContent() {
  const { range, setRange, dateFrom, dateTo, isDefaultRange } = useDateRangeFilter()
  const [courierId, setCourierId] = React.useState<string | undefined>(undefined)
  const [paymentType, setPaymentType] = React.useState<string | undefined>(undefined)
  const [sku, setSku] = React.useState("")
  const [selection, setSelection] = React.useState<Selection | null>(null)
  const [search, setSearch] = React.useState("")
  const debouncedSearch = useDebouncedValue(search)
  const drilldownRef = React.useRef<HTMLDivElement>(null)

  const filters = {
    courier_id: courierId,
    payment_type: paymentType,
    sku: sku || undefined,
    date_from: dateFrom,
    date_to: dateTo,
  }

  const couriersQuery = useCouriers()
  const fulfilmentQuery = useFulfilmentPerformance(filters)
  const fulfilmentHistory = useFulfilmentPerformanceHistory(filters)
  const deliveryQuery = useDeliveryPerformance(filters)
  const deliveryHistory = useDeliveryPerformanceHistory(filters)
  // Denominator for every percentage below: all orders under the SAME filters.
  const totalsQuery = useShipmentStatusAnalytics(filters)
  const totalOrders = totalsQuery.data?.total_orders

  const { page, pageSize, setPage, resetPage } = usePaginationState()
  const ordersQuery = useFulfilmentOrders({
    scope: selection?.scope ?? null,
    day: selection?.day,
    q: debouncedSearch || undefined,
    page,
    pageSize,
    enabled: selection !== null,
    ...filters,
  })

  function select(next: Selection) {
    const same = selection?.scope === next.scope && selection.day === next.day
    setSelection(same ? null : next)
    setSearch("")
    resetPage()
    if (!same) {
      window.setTimeout(
        () =>
          drilldownRef.current?.scrollIntoView?.({ behavior: "smooth", block: "start" }),
        50
      )
    }
  }

  function clear() {
    setSelection(null)
    setSearch("")
    resetPage()
  }

  function changeFilter(update: () => void) {
    update()
    resetPage()
  }

  const isScope = (scope: FulfilmentScope) =>
    selection?.scope === scope && selection.day === undefined

  const drilldownTitle = selection
    ? selection.day
      ? `${SCOPE_TITLES[selection.scope].split(" (")[0]} on ${formatDate(selection.day)}`
      : SCOPE_TITLES[selection.scope]
    : ""

  return (
    <div className="space-y-5">
      <PageHeader
        title="Fulfilment & Delivery Performance"
        description="How quickly orders leave the warehouse and reach customers. Click any card or chart bar to see the orders behind it."
      />

      <FilterBar
        dateRangeIsDefault={isDefaultRange}
        dateRange={range}
        onDateRangeChange={(value) => changeFilter(() => setRange(value))}
        statusValue={paymentType}
        onStatusChange={(value) => changeFilter(() => setPaymentType(value))}
        statusOptions={[
          { label: "COD", value: "cod" },
          { label: "Prepaid", value: "prepaid" },
        ]}
        statusLabel="Payment Type"
        extra={
          <>
            <Select
              value={courierId ?? ALL_COURIERS}
              onValueChange={(value) =>
                changeFilter(() =>
                  setCourierId(value === ALL_COURIERS ? undefined : value)
                )
              }
            >
              <SelectTrigger className="w-[180px]">
                <SelectValue placeholder="Shipping partner" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={ALL_COURIERS}>All shipping partners</SelectItem>
                {couriersQuery.data?.map((courier) => (
                  <SelectItem key={courier.id} value={courier.id}>
                    {courier.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Input
              value={sku}
              onChange={(event) => changeFilter(() => setSku(event.target.value))}
              placeholder="Filter by SKU"
              className="w-[160px]"
            />
          </>
        }
      />

      <section className="min-w-0 space-y-3">
        <div>
          <h3 className="text-base font-semibold">Average times</h3>
          <p className="text-muted-foreground text-xs">
            Fulfilment time runs from order confirmation to courier pickup; delivery time
            runs from courier pickup to delivery.
          </p>
        </div>
        <QueryStates
          isLoading={fulfilmentQuery.isLoading || deliveryQuery.isLoading}
          isError={fulfilmentQuery.isError || deliveryQuery.isError}
          error={fulfilmentQuery.error ?? deliveryQuery.error}
          data={
            fulfilmentQuery.data && deliveryQuery.data
              ? { f: fulfilmentQuery.data, d: deliveryQuery.data }
              : undefined
          }
          onRetry={() => {
            void fulfilmentQuery.refetch()
            void deliveryQuery.refetch()
          }}
        >
          {({ f, d }) => (
            <>
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
                <SelectableStatCard
                  label="Average Fulfilment Time"
                  value={formatDays(f.avg_fulfilment_days)}
                  detail={coverage(f.fulfilled_order_count, totalOrders)}
                  description="Order confirmed to picked up by the courier"
                  accent="blue"
                  selected={isScope("fulfilled")}
                  onClick={() => select({ scope: "fulfilled" })}
                />
                <SelectableStatCard
                  label="Average Delivery Time"
                  value={formatDays(d.overall_avg_delivery_days)}
                  detail={coverage(d.delivered_order_count, totalOrders)}
                  description="Courier pickup to delivered, all payment types"
                  accent="green"
                  selected={isScope("delivered")}
                  onClick={() => select({ scope: "delivered" })}
                />
                <SelectableStatCard
                  label="COD Average Delivery Time"
                  value={formatDays(d.cod_avg_delivery_days)}
                  detail={coverage(d.cod_delivered_order_count, totalOrders)}
                  description="Cash-on-delivery orders only"
                  accent="amber"
                  selected={isScope("cod_delivered")}
                  onClick={() => select({ scope: "cod_delivered" })}
                />
                <SelectableStatCard
                  label="Prepaid Average Delivery Time"
                  value={formatDays(d.prepaid_avg_delivery_days)}
                  detail={coverage(d.prepaid_delivered_order_count, totalOrders)}
                  description="Prepaid orders only"
                  accent="violet"
                  selected={isScope("prepaid_delivered")}
                  onClick={() => select({ scope: "prepaid_delivered" })}
                />
              </div>
              <div className="text-muted-foreground space-y-1 text-xs">
                <p>
                  The percentage on each card is the share of all{" "}
                  {totalOrders === undefined
                    ? "orders"
                    : `${totalOrders.toLocaleString("en-IN")} orders`}{" "}
                  in this period that the average is based on. This is sample coverage,
                  not delivery efficiency: it shows how much data backs each average.
                </p>
                {f.missing_pickup_timestamp_count > 0 && (
                  <p>
                    {plural(f.missing_pickup_timestamp_count, "order does", "orders do")}{" "}
                    not have a recorded courier pickup date. These orders are excluded
                    from the average fulfilment calculation.
                  </p>
                )}
                {d.missing_timestamp_count > 0 && (
                  <p>
                    {plural(
                      d.missing_timestamp_count,
                      "delivered order lacks",
                      "delivered orders lack"
                    )}{" "}
                    a pickup or delivery date. These orders are excluded from the average
                    delivery calculations.
                  </p>
                )}
                {!f.team_warehouse_attribution_available && (
                  <p>
                    Performance by team or warehouse is not available: orders are not
                    linked to a team or warehouse in the system.
                  </p>
                )}
              </div>
            </>
          )}
        </QueryStates>
      </section>

      <section className="space-y-3 rounded-xl border border-dashed p-4">
        <div>
          <h3 className="text-base font-semibold">Orders Awaiting Fulfilment</h3>
          <p className="text-muted-foreground text-xs">
            Work still to be done. These orders have not been picked up by a courier yet,
            so they are not part of any average above.
          </p>
        </div>
        <QueryStates
          isLoading={fulfilmentQuery.isLoading}
          isError={fulfilmentQuery.isError}
          error={fulfilmentQuery.error}
          data={fulfilmentQuery.data}
          onRetry={() => fulfilmentQuery.refetch()}
        >
          {(f) => (
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
              <SelectableStatCard
                label="Pending Fulfilment"
                value={f.pending_fulfilment_count}
                detail={ofAll(f.pending_fulfilment_count, totalOrders)}
                description="Ready to ship or waiting for pickup"
                accent="amber"
                selected={isScope("pending")}
                onClick={() => select({ scope: "pending" })}
              />
              <SelectableStatCard
                label="Pending for More Than 24 Hours"
                value={f.pending_over_24h_count}
                detail={ofAll(f.pending_over_24h_count, totalOrders)}
                description="Waiting longer than one day"
                accent="orange"
                selected={isScope("pending_24h")}
                onClick={() => select({ scope: "pending_24h" })}
              />
              <SelectableStatCard
                label="Pending for More Than 48 Hours"
                value={f.pending_over_48h_count}
                detail={ofAll(f.pending_over_48h_count, totalOrders)}
                description="Waiting longer than two days"
                accent="red"
                selected={isScope("pending_48h")}
                onClick={() => select({ scope: "pending_48h" })}
              />
            </div>
          )}
        </QueryStates>
      </section>

      <div className="grid gap-4 xl:grid-cols-2">
        <section className="bg-card min-w-0 space-y-3 rounded-xl border p-4">
          <div>
            <h3 className="text-base font-semibold">Daily Fulfilment Performance</h3>
            <p className="text-muted-foreground text-xs">
              Bars: orders picked up by a courier that day. Line: their average fulfilment
              time in days. Click a day to list those orders.
            </p>
          </div>
          <QueryStates
            isLoading={fulfilmentHistory.isLoading}
            isError={fulfilmentHistory.isError}
            error={fulfilmentHistory.error}
            data={fulfilmentHistory.data}
            onRetry={() => fulfilmentHistory.refetch()}
            isEmpty={(d) => d.daily.length === 0}
            emptyTitle="No orders were picked up in this range"
          >
            {(history) => {
              const data: ChartDatum[] = history.daily.map((point) => ({
                key: point.date,
                label: shortDay(point.date),
                value: point.fulfilled_order_count,
                secondary: point.avg_fulfilment_days,
                color: "#2563eb",
              }))
              return (
                <ClickableBarChart
                  ariaLabel="Orders fulfilled per day"
                  data={data}
                  selectedKey={
                    selection?.scope === "fulfilled" ? (selection.day ?? null) : null
                  }
                  onSelect={(day) => select({ scope: "fulfilled", day })}
                  valueLabel="Orders fulfilled"
                  secondaryLabel="Avg. fulfilment (days)"
                  showSecondaryLine
                />
              )
            }}
          </QueryStates>
        </section>

        <section className="bg-card min-w-0 space-y-3 rounded-xl border p-4">
          <div>
            <h3 className="text-base font-semibold">Delivery Performance</h3>
            <p className="text-muted-foreground text-xs">
              Bars: orders delivered that day. Line: their average delivery time in days.
              Click a day to list those orders.
            </p>
          </div>
          <QueryStates
            isLoading={deliveryHistory.isLoading}
            isError={deliveryHistory.isError}
            error={deliveryHistory.error}
            data={deliveryHistory.data}
            onRetry={() => deliveryHistory.refetch()}
            isEmpty={(d) => d.daily.length === 0}
            emptyTitle="No deliveries in this range"
          >
            {(history) => {
              const data: ChartDatum[] = history.daily.map((point) => ({
                key: point.date,
                label: shortDay(point.date),
                value: point.delivered_order_count,
                secondary: point.avg_delivery_days,
                color: "#65a30d",
              }))
              return (
                <ClickableBarChart
                  ariaLabel="Orders delivered per day"
                  data={data}
                  selectedKey={
                    selection?.scope === "delivered" ? (selection.day ?? null) : null
                  }
                  onSelect={(day) => select({ scope: "delivered", day })}
                  valueLabel="Orders delivered"
                  secondaryLabel="Avg. delivery (days)"
                  showSecondaryLine
                />
              )
            }}
          </QueryStates>
        </section>
      </div>

      <div ref={drilldownRef}>
        {selection && (
          <OrderDrilldown
            title={drilldownTitle}
            description="Same date range, shipping partner, payment type and SKU filters as the numbers above."
            query={ordersQuery}
            columns={orderColumns(selection.scope)}
            search={search}
            onSearchChange={(value) => {
              setSearch(value)
              resetPage()
            }}
            onPageChange={setPage}
            onClear={clear}
          />
        )}
      </div>

      <QueryStates
        isLoading={deliveryQuery.isLoading}
        isError={deliveryQuery.isError}
        error={deliveryQuery.error}
        data={deliveryQuery.data}
        onRetry={() => deliveryQuery.refetch()}
      >
        {(data) => (
          <section className="min-w-0 space-y-3">
            <div>
              <h3 className="text-base font-semibold">
                Delivery time by partner and region
              </h3>
              <p className="text-muted-foreground text-xs">
                Average days from courier pickup to delivery.
              </p>
            </div>
            <div className="grid gap-3 lg:grid-cols-2">
              <div className="bg-card overflow-hidden rounded-lg border">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Shipping partner</TableHead>
                      <TableHead className="text-right">Avg. delivery time</TableHead>
                      <TableHead className="text-right">Delivered</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {data.by_courier.length === 0 ? (
                      <TableRow>
                        <TableCell
                          colSpan={3}
                          className="text-muted-foreground text-center"
                        >
                          No delivered orders in this range
                        </TableCell>
                      </TableRow>
                    ) : (
                      data.by_courier.map((row) => (
                        <TableRow key={row.courier_key ?? row.courier_id ?? "unassigned"}>
                          <TableCell className="font-medium">
                            {row.courier_name}
                            <ServiceNames
                              names={row.service_names}
                              carrier={row.courier_name}
                            />
                          </TableCell>
                          <TableCell className="text-right tabular-nums">
                            {formatDays(row.avg_delivery_days)}
                          </TableCell>
                          <TableCell className="text-right tabular-nums">
                            {row.delivered_order_count}
                          </TableCell>
                        </TableRow>
                      ))
                    )}
                  </TableBody>
                </Table>
              </div>
              <div className="bg-card overflow-hidden rounded-lg border">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Destination state</TableHead>
                      <TableHead className="text-right">Avg. delivery time</TableHead>
                      <TableHead className="text-right">Delivered</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {data.by_zone.length === 0 ? (
                      <TableRow>
                        <TableCell
                          colSpan={3}
                          className="text-muted-foreground text-center"
                        >
                          No regional data in this range
                        </TableCell>
                      </TableRow>
                    ) : (
                      data.by_zone.map((row) => (
                        <TableRow key={row.zone}>
                          <TableCell className="font-medium">{row.zone}</TableCell>
                          <TableCell className="text-right tabular-nums">
                            {formatDays(row.avg_delivery_days)}
                          </TableCell>
                          <TableCell className="text-right tabular-nums">
                            {row.delivered_order_count}
                          </TableCell>
                        </TableRow>
                      ))
                    )}
                  </TableBody>
                </Table>
              </div>
            </div>
            {data.zone_is_state_based && (
              <p className={cn("text-muted-foreground text-xs")}>
                Regions are destination states, not courier billing zones
                {data.unmapped_zone_order_count > 0
                  ? `. ${plural(data.unmapped_zone_order_count, "order has", "orders have")} no recognisable state.`
                  : "."}
              </p>
            )}
          </section>
        )}
      </QueryStates>
    </div>
  )
}

/** `useDateRangeFilter` keeps the range in the URL (`useSearchParams`), which
 * Next.js requires to sit under a Suspense boundary for static prerendering.
 */
export default function FulfilmentPerformancePage() {
  return (
    <React.Suspense
      fallback={<div className="text-muted-foreground p-4 text-sm">Loading…</div>}
    >
      <FulfilmentPerformancePageContent />
    </React.Suspense>
  )
}

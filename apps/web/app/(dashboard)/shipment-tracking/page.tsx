"use client"

import * as React from "react"

import { ClickableDonut } from "@/components/shared/clickable-charts"
import type { DataTableColumn } from "@/components/shared/data-table"
import { FilterBar } from "@/components/shared/filter-bar"
import { OrderDrilldown, useDebouncedValue } from "@/components/shared/order-drilldown"
import { PageHeader } from "@/components/shared/page-header"
import { QueryStates } from "@/components/shared/query-states"
import { SelectableStatCard } from "@/components/shared/selectable-stat-card"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
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
import { formatDate, formatDateTime } from "@/lib/format"
import { formatShare } from "@/lib/percent"
import {
  groupOfStatus,
  paymentLabel,
  STATUS_GROUP_META,
  technicalStatusLabel,
} from "@/lib/shipment-labels"
import { STATUS_TONE_CLASSES } from "@/lib/status-styles"
import { cn } from "@/lib/utils"
import { useDateRangeFilter } from "@/lib/use-date-range-filter"
import { usePaginationState } from "@/lib/use-pagination"
import { useCouriers } from "@/services/couriers"
import {
  downloadShipmentStatusHistory,
  useShipmentStatusAnalytics,
  useShipmentStatusDrilldown,
  useShipmentStatusHistory,
} from "@/services/shipments"
import type {
  ShipmentStatusAnalytics,
  ShipmentStatusDrilldownRow,
  StatusGroupCount,
  StatusGroupKey,
  StatusKpiKey,
  UnifiedShipmentStatus,
} from "@/types/shipment"

const ALL_COURIERS = "__all__"

/** "72.9% of total orders" -- the share of every order in the current
 * filtered period; "—" when there are no orders (never 0% or NaN).
 */
function ofTotal(count: number, total: number): string {
  const share = formatShare(count, total)
  return share === "—" ? "—" : `${share} of total orders`
}

type Selection =
  { type: "kpi"; key: StatusKpiKey } | { type: "group"; key: StatusGroupKey }

const KPI_TITLES: Record<StatusKpiKey, string> = {
  total: "All orders",
  fulfilled: "Fulfilled orders",
  delivered: "Delivered orders",
  rto: "Return-to-origin (RTO) orders",
}

/** Use the backend's category counts; if an older API (no `status_groups`)
 * answers during a staged deploy, derive the same grouping from the
 * technical breakdown so the page never shows empty cards.
 */
function groupsOf(data: ShipmentStatusAnalytics): StatusGroupCount[] {
  if (data.status_groups && data.status_groups.length > 0) return data.status_groups
  const order = Object.keys(STATUS_GROUP_META) as StatusGroupKey[]
  return order.map((key) => {
    const rows = data.status_breakdown.filter((row) => groupOfStatus(row.status) === key)
    const count = rows.reduce((sum, row) => sum + row.count, 0)
    return {
      key,
      label: STATUS_GROUP_META[key].label,
      statuses: rows.map((row) => row.status),
      count,
      percent_of_total: data.total_orders
        ? Math.round((count / data.total_orders) * 10000) / 100
        : 0,
    }
  })
}

function headline(data: ShipmentStatusAnalytics) {
  const countOf = (statuses: string[]) =>
    data.status_breakdown
      .filter((row) => statuses.includes(row.status))
      .reduce((sum, row) => sum + row.count, 0)
  return {
    total: data.total_orders,
    fulfilled:
      data.total_fulfilled ??
      data.total_shipped_orders + countOf(["fulfilled_tracking_unavailable"]),
    delivered: data.total_delivered ?? countOf(["delivered"]),
    rto: data.total_rto ?? countOf(["rto_initiated", "rto_in_transit", "rto_delivered"]),
  }
}

function StatusPill({
  status,
}: {
  status: ShipmentStatusDrilldownRow["unified_status"]
}) {
  const meta = STATUS_GROUP_META[groupOfStatus(status)]
  return (
    <Badge
      variant="outline"
      className={cn(
        "rounded-md border-transparent font-semibold",
        STATUS_TONE_CLASSES[meta.tone]
      )}
    >
      {meta.label}
    </Badge>
  )
}

const drilldownColumns: DataTableColumn<ShipmentStatusDrilldownRow>[] = [
  {
    id: "order_number",
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
  { id: "customer", header: "Customer", cell: (row) => row.customer_name ?? "—" },
  {
    id: "sku",
    header: "Product (SKU)",
    cell: (row) => (
      <span className="line-clamp-2 max-w-48 text-xs">{row.sku_summary ?? "—"}</span>
    ),
  },
  { id: "payment", header: "Payment", cell: (row) => paymentLabel(row.payment_type) },
  { id: "partner", header: "Shipping partner", cell: (row) => row.courier_name ?? "—" },
  {
    id: "status",
    header: "Status",
    cell: (row) => (
      <div className="space-y-0.5">
        <StatusPill status={row.unified_status} />
        <span className="text-muted-foreground block text-xs">
          Original status: {technicalStatusLabel(row.unified_status)}
        </span>
      </div>
    ),
  },
  {
    id: "tracking",
    header: "Tracking",
    cell: (row) => (
      <div className="text-xs">
        <span className="block font-medium">{row.awb ?? "No tracking number"}</span>
        {row.current_location && (
          <span className="text-muted-foreground block">{row.current_location}</span>
        )}
        {row.last_tracking_update_at && (
          <span className="text-muted-foreground block">
            Updated {formatDateTime(row.last_tracking_update_at)}
          </span>
        )}
      </div>
    ),
  },
]

/** OMS SOW Module 2 — Shipment Tracking & Status Analytics, laid out for
 * management: four headline numbers, one chart and one plain-language
 * breakdown; every card, bar, segment and row opens the exact orders behind
 * it. Counts come from the backend unchanged (same formulas/denominators).
 */
function ShipmentTrackingPageContent() {
  const {
    range: dateRange,
    setRange: setDateRange,
    dateFrom,
    dateTo,
    isDefaultRange,
  } = useDateRangeFilter()
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
  const analyticsQuery = useShipmentStatusAnalytics(filters)
  const historyQuery = useShipmentStatusHistory(filters)

  const { page, pageSize, setPage, resetPage } = usePaginationState()
  const drilldownQuery = useShipmentStatusDrilldown({
    kpi: selection?.type === "kpi" ? selection.key : undefined,
    group: selection?.type === "group" ? selection.key : undefined,
    q: debouncedSearch || undefined,
    page,
    pageSize,
    enabled: selection !== null,
    ...filters,
  })

  function select(next: Selection) {
    const same = selection?.type === next.type && selection.key === next.key
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

  return (
    <div className="space-y-5">
      <PageHeader
        title="Shipment Tracking & Status Analytics"
        description="Where every order stands today. Click any number, chart section or status to see the orders behind it."
      />

      <FilterBar
        dateRangeIsDefault={isDefaultRange}
        dateRange={dateRange}
        onDateRangeChange={(range) => changeFilter(() => setDateRange(range))}
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

      <QueryStates
        isLoading={analyticsQuery.isLoading}
        isError={analyticsQuery.isError}
        error={analyticsQuery.error}
        data={analyticsQuery.data}
        onRetry={() => analyticsQuery.refetch()}
      >
        {(data) => {
          const groups = groupsOf(data)
          const kpis = headline(data)
          const untrackedCount =
            groups.find((g) => g.key === "tracking_pending")?.count ?? 0
          const isKpi = (key: StatusKpiKey) =>
            selection?.type === "kpi" && selection.key === key
          return (
            <>
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
                <SelectableStatCard
                  label="Total Orders"
                  value={kpis.total}
                  detail={ofTotal(kpis.total, kpis.total)}
                  description="Every order placed in this period"
                  accent="blue"
                  selected={isKpi("total")}
                  onClick={() => select({ type: "kpi", key: "total" })}
                />
                <SelectableStatCard
                  label="Total Fulfilled"
                  value={kpis.fulfilled}
                  detail={ofTotal(kpis.fulfilled, kpis.total)}
                  description="Handed to a courier or already fulfilled in Shopify"
                  accent="violet"
                  selected={isKpi("fulfilled")}
                  onClick={() => select({ type: "kpi", key: "fulfilled" })}
                />
                <SelectableStatCard
                  label="Total Delivered"
                  value={kpis.delivered}
                  detail={ofTotal(kpis.delivered, kpis.total)}
                  description="Successfully reached the customer"
                  accent="green"
                  selected={isKpi("delivered")}
                  onClick={() => select({ type: "kpi", key: "delivered" })}
                />
                <SelectableStatCard
                  label="Total RTO"
                  value={kpis.rto}
                  detail={ofTotal(kpis.rto, kpis.total)}
                  description="Being returned, or returned, to the warehouse"
                  accent="orange"
                  selected={isKpi("rto")}
                  onClick={() => select({ type: "kpi", key: "rto" })}
                />
              </div>

              <p className="text-muted-foreground text-xs">
                Order coverage:{" "}
                {data.orders_with_shipment_count ?? 0} order
                {data.orders_with_shipment_count === 1 ? "" : "s"} matched to a shipment,{" "}
                {data.orders_without_shipment_count ?? 0} order
                {data.orders_without_shipment_count === 1 ? "" : "s"} have no shipment record
                (fulfilled directly via Shopify, or not yet shipped) — these counts are
                order-level, so they will not line up 1:1 with a courier&apos;s own
                shipment-level export.
                {data.rto_breakdown && (
                  <>
                    {" "}
                    RTO breakdown: {data.rto_breakdown.rto_initiated} initiated,{" "}
                    {data.rto_breakdown.rto_in_transit} in transit,{" "}
                    {data.rto_breakdown.rto_delivered} delivered — compare against the matching
                    single status on the courier&apos;s side rather than the combined total
                    above.
                  </>
                )}
              </p>

              <div className="grid gap-4 xl:grid-cols-2">
                <section className="bg-card min-w-0 space-y-3 rounded-xl border p-4">
                  <div>
                    <h3 className="text-base font-semibold">Orders by shipment status</h3>
                    <p className="text-muted-foreground text-xs">
                      Click a section to list those orders.
                    </p>
                  </div>
                  <ClickableDonut
                    ariaLabel="Orders by shipment status"
                    centerLabel="orders"
                    selectedKey={selection?.type === "group" ? selection.key : null}
                    onSelect={(key) =>
                      select({ type: "group", key: key as StatusGroupKey })
                    }
                    data={groups.map((g) => ({
                      key: g.key,
                      label: g.label,
                      value: g.count,
                      color: STATUS_GROUP_META[g.key]?.color,
                    }))}
                  />
                </section>

                <section className="bg-card min-w-0 space-y-3 rounded-xl border p-4">
                  <div>
                    <h3 className="text-base font-semibold">Status breakdown</h3>
                    <p className="text-muted-foreground text-xs">
                      Every order is counted once. Click a row to list its orders.
                    </p>
                  </div>
                  <div className="overflow-hidden rounded-lg border">
                    <Table>
                      <TableHeader>
                        <TableRow>
                          <TableHead>Status</TableHead>
                          <TableHead className="text-right">Orders</TableHead>
                          <TableHead className="text-right">% of total</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {groups.map((group) => {
                          const active =
                            selection?.type === "group" && selection.key === group.key
                          return (
                            <TableRow
                              key={group.key}
                              tabIndex={0}
                              role="button"
                              aria-pressed={active}
                              className={cn(
                                "hover:bg-muted/50 cursor-pointer",
                                active && "bg-primary/10"
                              )}
                              onClick={() => select({ type: "group", key: group.key })}
                              onKeyDown={(event) => {
                                if (event.key === "Enter" || event.key === " ") {
                                  event.preventDefault()
                                  select({ type: "group", key: group.key })
                                }
                              }}
                            >
                              <TableCell className="font-medium">
                                <span className="flex items-center gap-2">
                                  <span
                                    className="size-2.5 shrink-0 rounded-sm"
                                    style={{
                                      backgroundColor:
                                        STATUS_GROUP_META[group.key]?.color,
                                    }}
                                  />
                                  {group.label}
                                </span>
                              </TableCell>
                              <TableCell className="text-right tabular-nums">
                                {group.count}
                              </TableCell>
                              <TableCell className="text-right tabular-nums">
                                {formatShare(group.count, kpis.total)}
                              </TableCell>
                            </TableRow>
                          )
                        })}
                      </TableBody>
                    </Table>
                  </div>
                </section>
              </div>

              <div className="text-muted-foreground space-y-1 text-xs">
                <p>
                  “Return to Origin” counts RTO Initiated and RTO Delivered; an RTO that
                  is still travelling back appears under “In Transit”, while “Total RTO”
                  above includes all three.
                </p>
                {untrackedCount > 0 && (
                  <p>
                    {untrackedCount} order(s) are marked fulfilled in Shopify but have no
                    shipment record here, so there is no tracking or delivery
                    confirmation. They are shown as “Fulfilled – Tracking Pending”.
                  </p>
                )}
              </div>
            </>
          )
        }}
      </QueryStates>

      <div ref={drilldownRef}>
        {selection && (
          <OrderDrilldown
            title={
              selection.type === "kpi"
                ? KPI_TITLES[selection.key]
                : (STATUS_GROUP_META[selection.key]?.label ?? selection.key)
            }
            description="Same date range, shipping partner, payment type and SKU filters as the numbers above."
            query={drilldownQuery}
            columns={drilldownColumns}
            search={search}
            onSearchChange={(value) => {
              setSearch(value)
              resetPage()
            }}
            onPageChange={setPage}
            onClear={clear}
            emptyTitle="No orders in this selection"
          />
        )}
      </div>

      <section className="min-w-0 space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <h3 className="text-base font-semibold">Daily status history</h3>
            <p className="text-muted-foreground text-xs">
              Orders placed each day, grouped by where they stand now.
            </p>
          </div>
          <Button
            variant="outline"
            size="sm"
            onClick={() => void downloadShipmentStatusHistory(filters)}
          >
            Export to Excel
          </Button>
        </div>
        <QueryStates
          isLoading={historyQuery.isLoading}
          isError={historyQuery.isError}
          error={historyQuery.error}
          data={historyQuery.data}
          onRetry={() => historyQuery.refetch()}
          isEmpty={(d) => d.daily.length === 0}
          emptyTitle="No shipment activity in this range"
        >
          {(history) => {
            const order = Object.keys(STATUS_GROUP_META) as StatusGroupKey[]
            const dayTotal = (point: unknown, key: StatusGroupKey) =>
              Object.entries(point as Record<string, unknown>)
                .filter(
                  ([field]) =>
                    field !== "date" &&
                    groupOfStatus(field as UnifiedShipmentStatus) === key
                )
                .reduce((sum, [, value]) => sum + Number(value ?? 0), 0)
            return (
              <div className="bg-card overflow-x-auto rounded-lg border">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Date</TableHead>
                      {order.map((key) => (
                        <TableHead key={key} className="text-right">
                          {STATUS_GROUP_META[key].label}
                        </TableHead>
                      ))}
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {history.daily.map((point) => (
                      <TableRow key={point.date}>
                        <TableCell>{formatDate(point.date)}</TableCell>
                        {order.map((key) => (
                          <TableCell key={key} className="text-right tabular-nums">
                            {dayTotal(point, key)}
                          </TableCell>
                        ))}
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            )
          }}
        </QueryStates>
      </section>
    </div>
  )
}

/** `useDateRangeFilter` keeps the range in the URL (`useSearchParams`), which
 * Next.js requires to sit under a Suspense boundary for static prerendering.
 */
export default function ShipmentTrackingPage() {
  return (
    <React.Suspense
      fallback={<div className="text-muted-foreground p-4 text-sm">Loading…</div>}
    >
      <ShipmentTrackingPageContent />
    </React.Suspense>
  )
}

"use client"

import * as React from "react"

import {
  ClickableBarChart,
  ClickableGroupedBarChart,
  type ChartDatum,
} from "@/components/shared/clickable-charts"
import type { DataTableColumn } from "@/components/shared/data-table"
import { FilterBar } from "@/components/shared/filter-bar"
import { OrderDrilldown, useDebouncedValue } from "@/components/shared/order-drilldown"
import { PageHeader } from "@/components/shared/page-header"
import { QueryStates } from "@/components/shared/query-states"
import { SelectableStatCard } from "@/components/shared/selectable-stat-card"
import { ServiceNames } from "@/components/shared/service-names"
import { Badge } from "@/components/ui/badge"
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
import {
  groupOfStatus,
  paymentLabel,
  STATUS_GROUP_META,
  technicalStatusLabel,
} from "@/lib/shipment-labels"
import { STATUS_TONE_CLASSES } from "@/lib/status-styles"
import { useDateRangeFilter } from "@/lib/use-date-range-filter"
import { usePaginationState } from "@/lib/use-pagination"
import { cn } from "@/lib/utils"
import {
  usePartnerAllocation,
  usePartnerOrders,
  usePartnerPerformance,
} from "@/services/shipments"
import type { PartnerMetric, PerformanceOrderRow } from "@/types/shipment"

const ALL_PARTNERS = "__all__"
const PARTNER_COLORS = [
  "#2563eb",
  "#65a30d",
  "#f97316",
  "#a855f7",
  "#14b8a6",
  "#ef4444",
  "#eab308",
]

interface Selection {
  metric: PartnerMetric
  /** A row's `courier_key`; undefined = every partner. */
  partner?: string
  /** Opened from the "Shipping Partners" card: shows the partner picker. */
  byPartner?: boolean
}

const METRIC_TITLES: Record<PartnerMetric, string> = {
  allocated: "Allocated orders",
  picked_up: "Picked-up orders",
  delivered: "Delivered orders",
  rto: "Return-to-origin (RTO) orders",
  cod_success: "Delivered COD orders",
  prepaid_success: "Delivered prepaid orders",
}

function pct(value: number | null): string {
  return value === null ? "—" : `${value}%`
}

function days(value: number | null): string {
  return value === null ? "—" : `${value} days`
}

const orderColumns: DataTableColumn<PerformanceOrderRow>[] = [
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
  {
    id: "status",
    header: "Status",
    cell: (row) => {
      const meta = STATUS_GROUP_META[groupOfStatus(row.unified_status)]
      return (
        <div className="space-y-0.5">
          <Badge
            variant="outline"
            className={cn(
              "rounded-md border-transparent font-semibold",
              STATUS_TONE_CLASSES[meta.tone]
            )}
          >
            {meta.label}
          </Badge>
          <span className="text-muted-foreground block text-xs">
            Original status: {technicalStatusLabel(row.unified_status)}
          </span>
        </div>
      )
    },
  },
  { id: "awb", header: "Tracking number", cell: (row) => row.awb ?? "—" },
  { id: "service", header: "Courier service", cell: (row) => row.courier_name ?? "—" },
  {
    id: "pickup",
    header: "Picked up",
    cell: (row) => (row.pickup_date ? formatDate(row.pickup_date) : "—"),
  },
  {
    id: "delivered",
    header: "Delivered",
    cell: (row) =>
      row.actual_delivery_date ? formatDate(row.actual_delivery_date) : "—",
  },
]

/** A table cell that opens the orders behind its own number; the click does
 * not also trigger the row's own "all orders" click.
 */
function MetricCell({
  children,
  label,
  onOpen,
}: {
  children: React.ReactNode
  label: string
  onOpen: () => void
}) {
  return (
    <TableCell className="text-right tabular-nums">
      <button
        type="button"
        aria-label={label}
        className="hover:bg-accent rounded px-1.5 py-0.5 underline-offset-2 hover:underline"
        onClick={(event) => {
          event.stopPropagation()
          onOpen()
        }}
      >
        {children}
      </button>
    </TableCell>
  )
}

/** OMS SOW Module 4 — Shipping Partner Allocation & Performance, laid out
 * for management. Carrier families (Blue Dart, Delhivery, …) keep their
 * original service names underneath; every card, row, metric and chart bar
 * opens the exact orders behind it.
 */
function ShippingPartnersPageContent() {
  const {
    range: dateRange,
    setRange: setDateRange,
    dateFrom,
    dateTo,
    isDefaultRange,
  } = useDateRangeFilter()
  const [paymentType, setPaymentType] = React.useState<string | undefined>(undefined)
  const [sku, setSku] = React.useState("")
  const [selection, setSelection] = React.useState<Selection | null>(null)
  const [search, setSearch] = React.useState("")
  const debouncedSearch = useDebouncedValue(search)
  const drilldownRef = React.useRef<HTMLDivElement>(null)

  const filters = {
    payment_type: paymentType,
    sku: sku || undefined,
    date_from: dateFrom,
    date_to: dateTo,
  }

  const allocationQuery = usePartnerAllocation(filters)
  const performanceQuery = usePartnerPerformance(filters)

  const { page, pageSize, setPage, resetPage } = usePaginationState()
  const ordersQuery = usePartnerOrders({
    metric: selection?.metric ?? null,
    partner: selection?.partner,
    q: debouncedSearch || undefined,
    page,
    pageSize,
    enabled: selection !== null,
    ...filters,
  })

  function select(next: Selection) {
    const same =
      selection?.metric === next.metric &&
      selection.partner === next.partner &&
      Boolean(selection.byPartner) === Boolean(next.byPartner)
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

  const partnerNames = new Map<string, string>()
  for (const row of allocationQuery.data?.partners ?? []) {
    partnerNames.set(row.courier_key, row.courier_name)
  }
  for (const row of performanceQuery.data?.partners ?? []) {
    partnerNames.set(row.courier_key, row.courier_name)
  }

  const drilldownTitle = selection
    ? selection.byPartner && selection.partner === undefined
      ? "Orders by shipping partner"
      : `${selection.partner ? `${partnerNames.get(selection.partner) ?? "Partner"} — ` : "All partners — "}${METRIC_TITLES[selection.metric]}`
    : ""

  return (
    <div className="space-y-5">
      <PageHeader
        title="Shipping Partner Performance"
        description="How each courier is doing. Click any card, row, number or chart bar to see the orders behind it."
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
          <Input
            value={sku}
            onChange={(event) => changeFilter(() => setSku(event.target.value))}
            placeholder="Filter by SKU"
            className="w-[160px]"
          />
        }
      />

      <section className="space-y-3">
        <div>
          <h3 className="text-base font-semibold">Order allocation</h3>
          <p className="text-muted-foreground text-xs">
            Orders with an identifiable courier, grouped by carrier. Each partner’s
            original courier services are listed under its name.
          </p>
        </div>
        <QueryStates
          isLoading={allocationQuery.isLoading}
          isError={allocationQuery.isError}
          error={allocationQuery.error}
          data={allocationQuery.data}
          onRetry={() => allocationQuery.refetch()}
          isEmpty={(d) => d.partners.length === 0}
          emptyTitle="No orders allocated to a shipping partner in this range"
        >
          {(data) => (
            <>
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                <SelectableStatCard
                  label="Total Orders Allocated"
                  value={data.total_orders_allocated}
                  description="Orders assigned to a shipping partner"
                  accent="blue"
                  selected={
                    selection?.metric === "allocated" &&
                    selection.partner === undefined &&
                    !selection.byPartner
                  }
                  onClick={() => select({ metric: "allocated" })}
                />
                <SelectableStatCard
                  label="Number of Shipping Partners"
                  value={data.partners.length}
                  description="Click to browse orders partner by partner"
                  accent="violet"
                  selected={Boolean(selection?.byPartner)}
                  onClick={() => select({ metric: "allocated", byPartner: true })}
                />
              </div>
              <div className="bg-card overflow-x-auto rounded-lg border">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Shipping partner</TableHead>
                      <TableHead className="text-right">Orders allocated</TableHead>
                      <TableHead className="text-right">Allocation %</TableHead>
                      <TableHead className="text-right">Avg. delivery time</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {data.partners.map((row) => {
                      const active =
                        selection?.partner === row.courier_key &&
                        selection.metric === "allocated"
                      return (
                        <TableRow
                          key={row.courier_key}
                          tabIndex={0}
                          role="button"
                          aria-pressed={active}
                          className={cn(
                            "hover:bg-muted/50 cursor-pointer",
                            active && "bg-primary/10"
                          )}
                          onClick={() =>
                            select({ metric: "allocated", partner: row.courier_key })
                          }
                          onKeyDown={(event) => {
                            if (event.key === "Enter" || event.key === " ") {
                              event.preventDefault()
                              select({ metric: "allocated", partner: row.courier_key })
                            }
                          }}
                        >
                          <TableCell className="font-medium">
                            {row.courier_name}
                            <ServiceNames
                              names={row.service_names}
                              carrier={row.courier_name}
                            />
                          </TableCell>
                          <TableCell className="text-right tabular-nums">
                            {row.orders_allocated}
                          </TableCell>
                          <TableCell className="text-right tabular-nums">
                            {row.allocation_percent}%
                          </TableCell>
                          <TableCell className="text-right tabular-nums">
                            {days(row.avg_delivery_days)}
                          </TableCell>
                        </TableRow>
                      )
                    })}
                  </TableBody>
                </Table>
              </div>
            </>
          )}
        </QueryStates>
      </section>

      <section className="space-y-3">
        <div>
          <h3 className="text-base font-semibold">Partner comparison charts</h3>
          <p className="text-muted-foreground text-xs">
            Success and RTO rates are measured against orders the courier has picked up.
            Click a bar to list those orders.
          </p>
        </div>
        <QueryStates
          isLoading={performanceQuery.isLoading}
          isError={performanceQuery.isError}
          error={performanceQuery.error}
          data={performanceQuery.data}
          onRetry={() => performanceQuery.refetch()}
          isEmpty={(d) => d.partners.length === 0}
          emptyTitle="No partner performance data in this range"
        >
          {(data) => {
            const colorOf = (index: number) =>
              PARTNER_COLORS[index % PARTNER_COLORS.length]
            const allocated: ChartDatum[] = data.partners.map((p, i) => ({
              key: p.courier_key,
              label: p.courier_name,
              value: p.total_allocated,
              color: colorOf(i),
            }))
            const success: ChartDatum[] = data.partners.map((p, i) => ({
              key: p.courier_key,
              label: p.courier_name,
              value: p.delivery_success_rate,
              color: colorOf(i),
            }))
            const rto: ChartDatum[] = data.partners.map((p) => ({
              key: p.courier_key,
              label: p.courier_name,
              value: p.rto_percentage,
              color: "#f97316",
            }))
            const selectedFor = (metric: PartnerMetric) =>
              selection?.metric === metric && !selection.byPartner
                ? (selection.partner ?? null)
                : null
            return (
              <div className="grid gap-4 xl:grid-cols-2">
                <div className="bg-card space-y-2 rounded-xl border p-4">
                  <h4 className="text-sm font-semibold">
                    Orders allocated by shipping partner
                  </h4>
                  <ClickableBarChart
                    ariaLabel="Orders allocated by shipping partner"
                    data={allocated}
                    selectedKey={selectedFor("allocated")}
                    onSelect={(key) => select({ metric: "allocated", partner: key })}
                    valueLabel="Orders"
                  />
                </div>
                <div className="bg-card space-y-2 rounded-xl border p-4">
                  <h4 className="text-sm font-semibold">
                    Delivery success rate by shipping partner
                  </h4>
                  <ClickableBarChart
                    ariaLabel="Delivery success rate by shipping partner"
                    data={success}
                    selectedKey={selectedFor("delivered")}
                    onSelect={(key) => select({ metric: "delivered", partner: key })}
                    valueLabel="Delivery success"
                    valueSuffix="%"
                  />
                </div>
                <div className="bg-card space-y-2 rounded-xl border p-4">
                  <h4 className="text-sm font-semibold">RTO rate by shipping partner</h4>
                  <ClickableBarChart
                    ariaLabel="RTO rate by shipping partner"
                    data={rto}
                    selectedKey={selectedFor("rto")}
                    onSelect={(key) => select({ metric: "rto", partner: key })}
                    valueLabel="RTO rate"
                    valueSuffix="%"
                  />
                </div>
                <div className="bg-card space-y-2 rounded-xl border p-4">
                  <h4 className="text-sm font-semibold">
                    COD vs Prepaid delivery success
                  </h4>
                  <ClickableGroupedBarChart
                    ariaLabel="COD versus prepaid delivery success by shipping partner"
                    data={data.partners.map((p) => ({
                      key: p.courier_key,
                      label: p.courier_name,
                      values: {
                        cod: p.cod_delivery_success_rate,
                        prepaid: p.prepaid_delivery_success_rate,
                      },
                    }))}
                    series={[
                      { key: "cod", label: "COD success", color: "#f59e0b" },
                      { key: "prepaid", label: "Prepaid success", color: "#2563eb" },
                    ]}
                    selected={
                      selection &&
                      selection.partner &&
                      (selection.metric === "cod_success" ||
                        selection.metric === "prepaid_success")
                        ? {
                            key: selection.partner,
                            series:
                              selection.metric === "cod_success" ? "cod" : "prepaid",
                          }
                        : null
                    }
                    onSelect={(key, series) =>
                      select({
                        metric: series === "cod" ? "cod_success" : "prepaid_success",
                        partner: key,
                      })
                    }
                    valueSuffix="%"
                  />
                </div>
              </div>
            )
          }}
        </QueryStates>
      </section>

      <section className="space-y-3">
        <div>
          <h3 className="text-base font-semibold">Partner performance comparison</h3>
          <p className="text-muted-foreground text-xs">
            Click a partner for all its orders, or click a number to see just those
            orders.
          </p>
        </div>
        <QueryStates
          isLoading={performanceQuery.isLoading}
          isError={performanceQuery.isError}
          error={performanceQuery.error}
          data={performanceQuery.data}
          onRetry={() => performanceQuery.refetch()}
          isEmpty={(d) => d.partners.length === 0}
          emptyTitle="No partner performance data in this range"
        >
          {(data) => (
            <>
              {!data.shipping_cost_available && (
                <p className="text-muted-foreground text-xs">
                  Shipping costs are not available yet. They will appear once courier
                  invoice data is connected.
                </p>
              )}
              <div className="bg-card overflow-x-auto rounded-lg border">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Shipping partner</TableHead>
                      <TableHead className="text-right">Allocated</TableHead>
                      <TableHead className="text-right">Picked up</TableHead>
                      <TableHead className="text-right">Delivered</TableHead>
                      <TableHead className="text-right">Delivery success %</TableHead>
                      <TableHead className="text-right">RTO %</TableHead>
                      <TableHead className="text-right">Avg. delivery days</TableHead>
                      <TableHead className="text-right">
                        Avg. fulfilment-to-delivery days
                      </TableHead>
                      <TableHead className="text-right">COD success %</TableHead>
                      <TableHead className="text-right">Prepaid success %</TableHead>
                      <TableHead className="text-right">Cost / delivered order</TableHead>
                      <TableHead className="text-right">Cost / RTO order</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {data.partners.map((row) => {
                      const open = (metric: PartnerMetric) =>
                        select({ metric, partner: row.courier_key })
                      const active = selection?.partner === row.courier_key
                      return (
                        <TableRow
                          key={row.courier_key}
                          className={cn(
                            "hover:bg-muted/50 cursor-pointer",
                            active && "bg-primary/10"
                          )}
                          onClick={() => open("allocated")}
                        >
                          <TableCell className="font-medium">
                            {row.courier_name}
                            <ServiceNames
                              names={row.service_names}
                              carrier={row.courier_name}
                            />
                          </TableCell>
                          <MetricCell
                            label={`${row.courier_name} allocated orders`}
                            onOpen={() => open("allocated")}
                          >
                            {row.total_allocated}
                          </MetricCell>
                          <MetricCell
                            label={`${row.courier_name} picked-up orders`}
                            onOpen={() => open("picked_up")}
                          >
                            {row.total_picked_up}
                          </MetricCell>
                          <MetricCell
                            label={`${row.courier_name} delivered orders`}
                            onOpen={() => open("delivered")}
                          >
                            {row.total_delivered}
                          </MetricCell>
                          <MetricCell
                            label={`${row.courier_name} delivered orders (success rate)`}
                            onOpen={() => open("delivered")}
                          >
                            {pct(row.delivery_success_rate)}
                          </MetricCell>
                          <MetricCell
                            label={`${row.courier_name} RTO orders`}
                            onOpen={() => open("rto")}
                          >
                            {pct(row.rto_percentage)}
                          </MetricCell>
                          <TableCell className="text-right tabular-nums">
                            {days(row.avg_delivery_days)}
                          </TableCell>
                          <TableCell className="text-right tabular-nums">
                            {days(row.avg_fulfilment_to_delivery_days)}
                          </TableCell>
                          <MetricCell
                            label={`${row.courier_name} delivered COD orders`}
                            onOpen={() => open("cod_success")}
                          >
                            {pct(row.cod_delivery_success_rate)}
                          </MetricCell>
                          <MetricCell
                            label={`${row.courier_name} delivered prepaid orders`}
                            onOpen={() => open("prepaid_success")}
                          >
                            {pct(row.prepaid_delivery_success_rate)}
                          </MetricCell>
                          <TableCell className="text-muted-foreground text-right">
                            {row.avg_shipping_cost_per_delivered_order === null
                              ? "Not available"
                              : row.avg_shipping_cost_per_delivered_order}
                          </TableCell>
                          <TableCell className="text-muted-foreground text-right">
                            {row.avg_shipping_cost_per_rto_order === null
                              ? "Not available"
                              : row.avg_shipping_cost_per_rto_order}
                          </TableCell>
                        </TableRow>
                      )
                    })}
                  </TableBody>
                </Table>
              </div>
            </>
          )}
        </QueryStates>
      </section>

      <div ref={drilldownRef}>
        {selection && (
          <OrderDrilldown
            title={drilldownTitle}
            description="Same date range, payment type and SKU filters as the numbers above."
            query={ordersQuery}
            columns={orderColumns}
            search={search}
            onSearchChange={(value) => {
              setSearch(value)
              resetPage()
            }}
            onPageChange={setPage}
            onClear={clear}
            filters={
              <Select
                value={selection.partner ?? ALL_PARTNERS}
                onValueChange={(value) => {
                  setSelection({
                    ...selection,
                    partner: value === ALL_PARTNERS ? undefined : value,
                  })
                  resetPage()
                }}
              >
                <SelectTrigger className="w-[200px]" aria-label="Shipping partner">
                  <SelectValue placeholder="Shipping partner" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={ALL_PARTNERS}>All shipping partners</SelectItem>
                  {[...partnerNames.entries()].map(([key, name]) => (
                    <SelectItem key={key} value={key}>
                      {name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            }
          />
        )}
      </div>
    </div>
  )
}

/** `useDateRangeFilter` keeps the range in the URL (`useSearchParams`), which
 * Next.js requires to sit under a Suspense boundary for static prerendering.
 */
export default function ShippingPartnersPage() {
  return (
    <React.Suspense
      fallback={<div className="text-muted-foreground p-4 text-sm">Loading…</div>}
    >
      <ShippingPartnersPageContent />
    </React.Suspense>
  )
}

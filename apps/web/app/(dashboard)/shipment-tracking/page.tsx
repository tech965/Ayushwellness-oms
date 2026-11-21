"use client"

import * as React from "react"
import { CheckCircle2, PackageCheck, RotateCcw, Timer, Truck } from "lucide-react"

import { DataTable, type DataTableColumn } from "@/components/shared/data-table"
import { FilterBar } from "@/components/shared/filter-bar"
import { PageHeader } from "@/components/shared/page-header"
import { PaginationBar } from "@/components/shared/pagination-bar"
import { QueryStates } from "@/components/shared/query-states"
import { StatTile } from "@/components/shared/stat-tile"
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
import { formatDate, formatMoney, formatRate } from "@/lib/format"
import { useDateRangeFilter } from "@/lib/use-date-range-filter"
import { usePaginationState } from "@/lib/use-pagination"
import { useCouriers } from "@/services/couriers"
import {
  downloadShipmentStatusHistory,
  useShipmentStatusAnalytics,
  useShipmentStatusDrilldown,
  useShipmentStatusHistory,
} from "@/services/shipments"
import {
  UNIFIED_SHIPMENT_STATUS_OPTIONS,
  type ShipmentStatusDrilldownRow,
  type UnifiedShipmentStatus,
} from "@/types/shipment"

const ALL_COURIERS = "__all__"

/** OMS SOW Module 2 — Shipment Tracking & Status Analytics. Reads
 * entirely from locally stored, already-synced order/shipment data (the
 * existing Shiprocket webhook/pull pipeline); this is a "stored data"
 * screen, same as `/shipments`, `/ndr`, `/rto` -- no `LiveModeGate` is
 * needed because nothing here is replaced in live-data mode (the
 * backend endpoints 403 with a clear reason in that mode instead; see
 * `QueryStates`' error rendering below).
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
  const [selectedStatus, setSelectedStatus] =
    React.useState<UnifiedShipmentStatus | null>(null)

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
    unifiedStatus: selectedStatus ?? "ready_to_ship",
    page,
    pageSize,
    enabled: selectedStatus !== null,
    ...filters,
  })

  function selectStatus(status: UnifiedShipmentStatus) {
    setSelectedStatus(status)
    resetPage()
  }

  const drilldownColumns: DataTableColumn<ShipmentStatusDrilldownRow>[] = [
    { id: "order_number", header: "Order #", cell: (row) => row.order_number },
    {
      id: "order_datetime",
      header: "Order Date",
      cell: (row) => formatDate(row.order_datetime),
    },
    { id: "customer_name", header: "Customer", cell: (row) => row.customer_name ?? "—" },
    { id: "sku_summary", header: "SKU", cell: (row) => row.sku_summary ?? "—" },
    { id: "payment_type", header: "Payment", cell: (row) => row.payment_type },
    { id: "courier_name", header: "Courier", cell: (row) => row.courier_name ?? "—" },
    { id: "awb", header: "AWB", cell: (row) => row.awb ?? "—" },
    {
      id: "total_amount",
      header: "Amount",
      cell: (row) => formatMoney(row.total_amount),
    },
  ]

  return (
    <div className="space-y-4">
      <PageHeader
        title="Shipment Tracking & Status Analytics"
        description="Unified status funnel across every shipping partner — pickup, in-transit, delivery and RTO rates, filterable by date, partner, payment mode and SKU."
      />

      <FilterBar
        dateRangeIsDefault={isDefaultRange}
        dateRange={dateRange}
        onDateRangeChange={(range) => {
          setDateRange(range)
          resetPage()
        }}
        statusValue={paymentType}
        onStatusChange={(value) => {
          setPaymentType(value)
          resetPage()
        }}
        statusOptions={[
          { label: "COD", value: "cod" },
          { label: "Prepaid", value: "prepaid" },
        ]}
        statusLabel="Payment Type"
        extra={
          <>
            <Select
              value={courierId ?? ALL_COURIERS}
              onValueChange={(value) => {
                setCourierId(value === ALL_COURIERS ? undefined : value)
                resetPage()
              }}
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
              onChange={(event) => {
                setSku(event.target.value)
                resetPage()
              }}
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
          const untrackedCount =
            data.status_breakdown.find(
              (row) => row.status === "fulfilled_tracking_unavailable"
            )?.count ?? 0
          return (
            <>
              <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
                <StatTile
                  label="Pickup %"
                  value={formatRate(data.pickup_rate, data.total_eligible_orders)}
                  icon={Truck}
                  accent="blue"
                  subtext={`of ${data.total_eligible_orders} eligible orders`}
                />
                <StatTile
                  label="In-Transit %"
                  value={formatRate(data.in_transit_rate, data.total_shipped_orders)}
                  icon={Timer}
                  accent="amber"
                  subtext={`of ${data.total_shipped_orders} shipped orders`}
                />
                <StatTile
                  label="Out for Delivery %"
                  value={formatRate(
                    data.out_for_delivery_rate,
                    data.total_shipped_orders
                  )}
                  icon={PackageCheck}
                  accent="violet"
                  subtext={`of ${data.total_shipped_orders} shipped orders`}
                />
                <StatTile
                  label="Delivery %"
                  value={formatRate(data.delivery_rate, data.total_shipped_orders)}
                  icon={CheckCircle2}
                  accent="emerald"
                  subtext={`of ${data.total_shipped_orders} shipped orders`}
                />
                <StatTile
                  label="RTO %"
                  value={formatRate(data.rto_rate, data.total_shipped_orders)}
                  icon={RotateCcw}
                  accent="orange"
                  subtext={`of ${data.total_shipped_orders} shipped orders`}
                />
              </div>

              {untrackedCount > 0 && (
                <p className="text-muted-foreground text-xs">
                  {untrackedCount} order(s) are marked fulfilled in Shopify but have no
                  shipment record here, so there is no tracking or delivery confirmation.
                  They are shown as “Fulfilled – Tracking Unavailable” and are excluded
                  from the Pickup, In-Transit, Delivery and RTO percentages.
                </p>
              )}
              <div className="bg-card overflow-hidden rounded-lg border">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Status</TableHead>
                      <TableHead className="text-right">Orders</TableHead>
                      <TableHead className="text-right">% of Total</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {data.status_breakdown.map((row) => {
                      const option = UNIFIED_SHIPMENT_STATUS_OPTIONS.find(
                        (o) => o.value === row.status
                      )
                      return (
                        <TableRow
                          key={row.status}
                          className="hover:bg-muted/50 cursor-pointer"
                          onClick={() => selectStatus(row.status)}
                          aria-selected={selectedStatus === row.status}
                        >
                          <TableCell className="font-medium">
                            {option?.label ?? row.status}
                          </TableCell>
                          <TableCell className="text-right tabular-nums">
                            {row.count}
                          </TableCell>
                          <TableCell className="text-right tabular-nums">
                            {row.percent_of_total}%
                          </TableCell>
                        </TableRow>
                      )
                    })}
                  </TableBody>
                </Table>
              </div>
            </>
          )
        }}
      </QueryStates>

      {selectedStatus && (
        <div className="space-y-3">
          <h3 className="text-sm font-semibold">
            Orders —{" "}
            {
              UNIFIED_SHIPMENT_STATUS_OPTIONS.find((o) => o.value === selectedStatus)
                ?.label
            }
          </h3>
          <QueryStates
            isLoading={drilldownQuery.isLoading}
            isError={drilldownQuery.isError}
            error={drilldownQuery.error}
            data={drilldownQuery.data}
            onRetry={() => drilldownQuery.refetch()}
            isEmpty={(d) => d.data.length === 0}
            emptyTitle="No orders in this status"
          >
            {(result) => (
              <>
                <DataTable
                  columns={drilldownColumns}
                  data={result.data}
                  rowKey={(row) => row.order_id}
                />
                <PaginationBar meta={result.meta} onPageChange={setPage} />
              </>
            )}
          </QueryStates>
        </div>
      )}

      <div className="space-y-3">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-semibold">Historical Shipment Status Report</h3>
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
          {(history) => (
            <div className="bg-card overflow-x-auto rounded-lg border">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Date</TableHead>
                    {UNIFIED_SHIPMENT_STATUS_OPTIONS.map((option) => (
                      <TableHead key={option.value} className="text-right">
                        {option.label}
                      </TableHead>
                    ))}
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {history.daily.map((point) => (
                    <TableRow key={point.date}>
                      <TableCell>{formatDate(point.date)}</TableCell>
                      {UNIFIED_SHIPMENT_STATUS_OPTIONS.map((option) => (
                        <TableCell key={option.value} className="text-right tabular-nums">
                          {point[option.value]}
                        </TableCell>
                      ))}
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}
        </QueryStates>
      </div>
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

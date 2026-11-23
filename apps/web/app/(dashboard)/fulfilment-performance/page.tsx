"use client"

import * as React from "react"
import { AlertTriangle, CheckCircle2, Clock, Hourglass, Timer, Truck } from "lucide-react"

import { FilterBar } from "@/components/shared/filter-bar"
import { PageHeader } from "@/components/shared/page-header"
import { QueryStates } from "@/components/shared/query-states"
import { ServiceNames } from "@/components/shared/service-names"
import { StatTile } from "@/components/shared/stat-tile"
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
import { useDateRangeFilter } from "@/lib/use-date-range-filter"
import { useCouriers } from "@/services/couriers"
import {
  useDeliveryPerformance,
  useDeliveryPerformanceHistory,
  useFulfilmentPerformance,
  useFulfilmentPerformanceHistory,
} from "@/services/shipments"

const ALL_COURIERS = "__all__"

/** Unavailable values render as an em dash -- never a misleading 0. */
function days(value: number | null | undefined): string {
  return value === null || value === undefined ? "—" : `${value.toFixed(2)} d`
}

/** OMS SOW Module 3 — Fulfilment & Delivery Performance. Reads only
 * locally stored order/shipment data (same "stored data" screen as
 * Module 2/4). Team/warehouse attribution genuinely doesn't exist in the
 * data model, and zones are destination states, so those sections show
 * the backend's stated reason instead of invented numbers.
 */
function FulfilmentPerformancePageContent() {
  const { range, setRange, dateFrom, dateTo, isDefaultRange } = useDateRangeFilter()
  const [courierId, setCourierId] = React.useState<string | undefined>(undefined)
  const [paymentType, setPaymentType] = React.useState<string | undefined>(undefined)
  const [sku, setSku] = React.useState("")

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

  return (
    <div className="space-y-4">
      <PageHeader
        title="Fulfilment & Delivery Performance"
        description="Internal fulfilment speed, pending orders and courier delivery performance — filterable by date, shipping partner, payment mode and SKU."
      />

      <FilterBar
        dateRangeIsDefault={isDefaultRange}
        dateRange={range}
        onDateRangeChange={setRange}
        statusValue={paymentType}
        onStatusChange={setPaymentType}
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
                setCourierId(value === ALL_COURIERS ? undefined : value)
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
              onChange={(event) => setSku(event.target.value)}
              placeholder="Filter by SKU"
              className="w-[160px]"
            />
          </>
        }
      />

      <div className="space-y-3">
        <h3 className="text-sm font-semibold">Internal Order Fulfilment</h3>
        <QueryStates
          isLoading={fulfilmentQuery.isLoading}
          isError={fulfilmentQuery.isError}
          error={fulfilmentQuery.error}
          data={fulfilmentQuery.data}
          onRetry={() => fulfilmentQuery.refetch()}
        >
          {(data) => (
            <>
              <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
                <StatTile
                  label="Avg. Fulfilment Time"
                  value={days(data.avg_fulfilment_days)}
                  icon={Timer}
                  accent="blue"
                  subtext={`${data.fulfilled_order_count} fulfilled orders`}
                />
                <StatTile
                  label="Pending Fulfilment"
                  value={data.pending_fulfilment_count}
                  icon={Hourglass}
                  accent="amber"
                />
                <StatTile
                  label="Pending > 24 hours"
                  value={data.pending_over_24h_count}
                  icon={Clock}
                  accent="orange"
                />
                <StatTile
                  label="Pending > 48 hours"
                  value={data.pending_over_48h_count}
                  icon={AlertTriangle}
                  accent="orange"
                />
              </div>
              {data.missing_pickup_timestamp_count > 0 && (
                <p className="text-muted-foreground text-xs">
                  {data.missing_pickup_timestamp_count} order(s) have no pickup timestamp
                  and are excluded from the average.
                </p>
              )}
              {!data.team_warehouse_attribution_available && (
                <p className="text-muted-foreground text-xs">
                  By team/warehouse: not available —{" "}
                  {data.team_warehouse_attribution_note}
                </p>
              )}
            </>
          )}
        </QueryStates>
        <QueryStates
          isLoading={fulfilmentHistory.isLoading}
          isError={fulfilmentHistory.isError}
          error={fulfilmentHistory.error}
          data={fulfilmentHistory.data}
          onRetry={() => fulfilmentHistory.refetch()}
          isEmpty={(d) => d.daily.length === 0}
          emptyTitle="No fulfilments in this range"
        >
          {(history) => (
            <div className="bg-card overflow-hidden rounded-lg border">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Date (pickup day)</TableHead>
                    <TableHead className="text-right">Avg. Fulfilment Time</TableHead>
                    <TableHead className="text-right">Fulfilled Orders</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {history.daily.map((point) => (
                    <TableRow key={point.date}>
                      <TableCell>{formatDate(point.date)}</TableCell>
                      <TableCell className="text-right tabular-nums">
                        {days(point.avg_fulfilment_days)}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">
                        {point.fulfilled_order_count}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}
        </QueryStates>
      </div>

      <div className="space-y-3">
        <h3 className="text-sm font-semibold">Delivery Performance</h3>
        <QueryStates
          isLoading={deliveryQuery.isLoading}
          isError={deliveryQuery.isError}
          error={deliveryQuery.error}
          data={deliveryQuery.data}
          onRetry={() => deliveryQuery.refetch()}
        >
          {(data) => (
            <>
              <div className="grid grid-cols-2 gap-3 md:grid-cols-3">
                <StatTile
                  label="Avg. Delivery Days"
                  value={days(data.overall_avg_delivery_days)}
                  icon={Truck}
                  accent="blue"
                  subtext={`${data.delivered_order_count} delivered orders`}
                />
                <StatTile
                  label="COD Avg. Delivery"
                  value={days(data.cod_avg_delivery_days)}
                  icon={CheckCircle2}
                  accent="emerald"
                  subtext={`${data.cod_delivered_order_count} delivered`}
                />
                <StatTile
                  label="Prepaid Avg. Delivery"
                  value={days(data.prepaid_avg_delivery_days)}
                  icon={CheckCircle2}
                  accent="violet"
                  subtext={`${data.prepaid_delivered_order_count} delivered`}
                />
              </div>
              {data.missing_timestamp_count > 0 && (
                <p className="text-muted-foreground text-xs">
                  {data.missing_timestamp_count} delivered order(s) lack a pickup or
                  delivery timestamp and are excluded from these averages.
                </p>
              )}
              <div className="grid gap-3 lg:grid-cols-2">
                <div className="bg-card overflow-hidden rounded-lg border">
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>Shipping Partner</TableHead>
                        <TableHead className="text-right">Avg. Delivery Days</TableHead>
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
                              <ServiceNames names={row.service_names} carrier={row.courier_name} />
                            </TableCell>
                            <TableCell className="text-right tabular-nums">
                              {days(row.avg_delivery_days)}
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
                        <TableHead>Zone (destination state)</TableHead>
                        <TableHead className="text-right">Avg. Delivery Days</TableHead>
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
                            No zone data in this range
                          </TableCell>
                        </TableRow>
                      ) : (
                        data.by_zone.map((row) => (
                          <TableRow key={row.zone}>
                            <TableCell className="font-medium">{row.zone}</TableCell>
                            <TableCell className="text-right tabular-nums">
                              {days(row.avg_delivery_days)}
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
                <p className="text-muted-foreground text-xs">
                  Zones are destination states, not courier billing zones
                  {data.unmapped_zone_order_count > 0
                    ? ` — ${data.unmapped_zone_order_count} order(s) have no recognisable state.`
                    : "."}
                </p>
              )}
            </>
          )}
        </QueryStates>
        <QueryStates
          isLoading={deliveryHistory.isLoading}
          isError={deliveryHistory.isError}
          error={deliveryHistory.error}
          data={deliveryHistory.data}
          onRetry={() => deliveryHistory.refetch()}
          isEmpty={(d) => d.daily.length === 0}
          emptyTitle="No deliveries in this range"
        >
          {(history) => (
            <div className="bg-card overflow-hidden rounded-lg border">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Date</TableHead>
                    <TableHead className="text-right">Avg. Delivery Days</TableHead>
                    <TableHead className="text-right">Delivered Orders</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {history.daily.map((point) => (
                    <TableRow key={point.date}>
                      <TableCell>{formatDate(point.date)}</TableCell>
                      <TableCell className="text-right tabular-nums">
                        {days(point.avg_delivery_days)}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">
                        {point.delivered_order_count}
                      </TableCell>
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
export default function FulfilmentPerformancePage() {
  return (
    <React.Suspense
      fallback={<div className="text-muted-foreground p-4 text-sm">Loading…</div>}
    >
      <FulfilmentPerformancePageContent />
    </React.Suspense>
  )
}

"use client"

import * as React from "react"
import { AlertTriangle, PackageCheck, Percent, Truck } from "lucide-react"

import { FilterBar, type DateRangeValue } from "@/components/shared/filter-bar"
import { PageHeader } from "@/components/shared/page-header"
import { QueryStates } from "@/components/shared/query-states"
import { StatTile } from "@/components/shared/stat-tile"
import { Input } from "@/components/ui/input"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { usePartnerAllocation, usePartnerPerformance } from "@/services/shipments"

function pct(value: number | null): string {
  return value === null ? "—" : `${value}%`
}

function days(value: number | null): string {
  return value === null ? "—" : `${value}d`
}

/** OMS SOW Module 4 — Shipping Partner Allocation & Performance Analysis.
 * Section A (allocation table) and Sections B/C (the comparison table,
 * which doubles as the Decision-Making Dashboard — see
 * `ShippingPartnerPerformanceService`'s module docstring for why there's
 * no separate backend call for Section C). Reads entirely from locally
 * stored order/shipment data — same "stored data" screen as Module 2/3's
 * pages, no `LiveModeGate` needed.
 */
export default function ShippingPartnersPage() {
  const [dateRange, setDateRange] = React.useState<DateRangeValue>({})
  const [paymentType, setPaymentType] = React.useState<string | undefined>(undefined)
  const [sku, setSku] = React.useState("")

  const filters = {
    payment_type: paymentType,
    sku: sku || undefined,
    date_from: dateRange.from?.toISOString(),
    date_to: dateRange.to?.toISOString(),
  }

  const allocationQuery = usePartnerAllocation(filters)
  const performanceQuery = usePartnerPerformance(filters)

  return (
    <div className="space-y-4">
      <PageHeader
        title="Shipping Partners"
        description="Order allocation and performance comparison across every registered shipping partner — allocation %, delivery success rate, RTO %, COD/Prepaid performance. Unintegrated partners (no courier record yet) simply don't appear."
      />

      <FilterBar
        dateRange={dateRange}
        onDateRangeChange={setDateRange}
        statusValue={paymentType}
        onStatusChange={setPaymentType}
        statusOptions={[
          { label: "COD", value: "cod" },
          { label: "Prepaid", value: "prepaid" },
        ]}
        statusLabel="Payment Type"
        extra={
          <Input
            value={sku}
            onChange={(event) => setSku(event.target.value)}
            placeholder="Filter by SKU"
            className="w-[160px]"
          />
        }
      />

      <div className="space-y-3">
        <h3 className="text-sm font-semibold">Order Allocation Dashboard</h3>
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
              <div className="grid grid-cols-2 gap-3 md:grid-cols-3">
                <StatTile
                  label="Total Orders Allocated"
                  value={data.total_orders_allocated}
                  icon={Truck}
                  accent="blue"
                />
                <StatTile
                  label="Shipping Partners"
                  value={data.partners.length}
                  icon={PackageCheck}
                  accent="violet"
                />
                <StatTile
                  label="Total Allocation"
                  value={`${data.total_allocation_percent}%`}
                  icon={Percent}
                  accent="emerald"
                  subtext="sums to 100% across all partners"
                />
              </div>
              <div className="bg-card overflow-hidden rounded-lg border">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Shipping Partner</TableHead>
                      <TableHead className="text-right">Orders Allocated</TableHead>
                      <TableHead className="text-right">Allocation %</TableHead>
                      <TableHead className="text-right">Avg. Delivery Days</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {data.partners.map((row) => (
                      <TableRow key={row.courier_id}>
                        <TableCell className="font-medium">{row.courier_name}</TableCell>
                        <TableCell className="text-right tabular-nums">{row.orders_allocated}</TableCell>
                        <TableCell className="text-right tabular-nums">{row.allocation_percent}%</TableCell>
                        <TableCell className="text-right tabular-nums">{days(row.avg_delivery_days)}</TableCell>
                      </TableRow>
                    ))}
                    <TableRow className="bg-muted/30 font-semibold">
                      <TableCell>Total</TableCell>
                      <TableCell className="text-right tabular-nums">{data.total_orders_allocated}</TableCell>
                      <TableCell className="text-right tabular-nums">{data.total_allocation_percent}%</TableCell>
                      <TableCell className="text-right">—</TableCell>
                    </TableRow>
                  </TableBody>
                </Table>
              </div>
            </>
          )}
        </QueryStates>
      </div>

      <div className="space-y-3">
        <h3 className="text-sm font-semibold">
          Shipping Partner Performance Comparison
          <span className="text-muted-foreground ml-2 text-xs font-normal">
            (also the Decision-Making view — compare partners directly below)
          </span>
        </h3>
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
                <div className="bg-amber-50 text-amber-900 dark:bg-amber-950 dark:text-amber-200 flex items-start gap-2 rounded-lg border border-amber-200 p-3 text-sm dark:border-amber-800">
                  <AlertTriangle className="mt-0.5 size-4 shrink-0" />
                  <span>{data.shipping_cost_unavailable_reason}</span>
                </div>
              )}
              <div className="bg-card overflow-x-auto rounded-lg border">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Shipping Partner</TableHead>
                      <TableHead className="text-right">Allocated</TableHead>
                      <TableHead className="text-right">Picked Up</TableHead>
                      <TableHead className="text-right">Delivered</TableHead>
                      <TableHead className="text-right">Delivery Success %</TableHead>
                      <TableHead className="text-right">RTO %</TableHead>
                      <TableHead className="text-right">Avg. Delivery Days</TableHead>
                      <TableHead className="text-right">Avg. Fulfilment-to-Delivery Days</TableHead>
                      <TableHead className="text-right">COD Success %</TableHead>
                      <TableHead className="text-right">Prepaid Success %</TableHead>
                      <TableHead className="text-right">Cost / Delivered Order</TableHead>
                      <TableHead className="text-right">Cost / RTO Order</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {data.partners.map((row) => (
                      <TableRow key={row.courier_id}>
                        <TableCell className="font-medium">{row.courier_name}</TableCell>
                        <TableCell className="text-right tabular-nums">{row.total_allocated}</TableCell>
                        <TableCell className="text-right tabular-nums">{row.total_picked_up}</TableCell>
                        <TableCell className="text-right tabular-nums">{row.total_delivered}</TableCell>
                        <TableCell className="text-right tabular-nums">{pct(row.delivery_success_rate)}</TableCell>
                        <TableCell className="text-right tabular-nums">{pct(row.rto_percentage)}</TableCell>
                        <TableCell className="text-right tabular-nums">{days(row.avg_delivery_days)}</TableCell>
                        <TableCell className="text-right tabular-nums">
                          {days(row.avg_fulfilment_to_delivery_days)}
                        </TableCell>
                        <TableCell className="text-right tabular-nums">{pct(row.cod_delivery_success_rate)}</TableCell>
                        <TableCell className="text-right tabular-nums">
                          {pct(row.prepaid_delivery_success_rate)}
                        </TableCell>
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
                    ))}
                  </TableBody>
                </Table>
              </div>
            </>
          )}
        </QueryStates>
      </div>
    </div>
  )
}

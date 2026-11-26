"use client"

import * as React from "react"
import {
  AlertTriangle,
  ArrowDownToLine,
  ArrowUpFromLine,
  Boxes,
  Lock,
  PackageCheck,
  PackageX,
  RotateCcw,
  ShieldQuestion,
  Warehouse,
} from "lucide-react"

import { DateRangePicker, type DateRangeValue } from "@/components/shared/date-range-picker"
import { QueryStates } from "@/components/shared/query-states"
import { StatTile } from "@/components/shared/stat-tile"
import { useInventoryDashboard } from "@/services/inventory"

/** Module 1.1/Module 1 — the Inventory dashboard. Every figure comes
 * from `GET /inventory/dashboard` (a live aggregate over
 * `ProductVariant`/`InventoryMovement`/`RTO` — see the backend's
 * `InventoryService.get_dashboard`), never hardcoded or sample data.
 * `opening_stock_boxes` is all-time and unaffected by the date range
 * below; only the inward/outward tiles are scoped to it.
 *
 * "Available Stock" (physical, on-hand), "Reserved" (soft-held by
 * active legacy order reservations) and "Available to Sell" (physical
 * minus reserved) are three distinct tiles — never collapsed into one
 * number. "RTO In Transit" (still with the courier) and "RTO Awaiting
 * Verification" (courier-delivered, not yet confirmed received by
 * warehouse staff) are likewise kept separate — neither counts toward
 * "Available Stock".
 */
export function InventoryDashboard() {
  const [range, setRange] = React.useState<DateRangeValue>({})
  const query = useInventoryDashboard({
    date_from: range.from ? range.from.toISOString() : undefined,
    date_to: range.to ? range.to.toISOString() : undefined,
  })

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-lg font-semibold">Dashboard</h2>
        <DateRangePicker value={range} onChange={setRange} />
      </div>
      <QueryStates
        isLoading={query.isLoading}
        isError={query.isError}
        error={query.error}
        data={query.data}
        onRetry={() => void query.refetch()}
      >
        {(data) => (
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
            <StatTile
              label="Total Products"
              value={data.total_products.toLocaleString()}
              icon={Boxes}
              accent="blue"
              subtext={`${data.total_skus.toLocaleString()} SKUs`}
            />
            <StatTile
              label="Available Stock"
              value={data.physical_stock_boxes.toLocaleString()}
              icon={PackageCheck}
              accent="emerald"
              subtext={`${data.total_available_packets.toLocaleString()} units, physical/on-hand`}
            />
            <StatTile
              label="Reserved"
              value={data.reserved_stock_boxes.toLocaleString()}
              icon={Lock}
              accent="slate"
              subtext="Held for pending orders, not yet dispatched"
            />
            <StatTile
              label="Available to Sell"
              value={data.available_to_sell_boxes.toLocaleString()}
              icon={Boxes}
              accent="blue"
              subtext="Physical minus reserved"
            />
            <StatTile
              label="Opening Stock"
              value={data.opening_stock_boxes.toLocaleString()}
              icon={Warehouse}
              accent="slate"
              subtext="All-time, before any movement"
            />
            <StatTile
              label="RTO In Transit"
              value={data.rto_in_transit_boxes.toLocaleString()}
              icon={RotateCcw}
              accent="orange"
              subtext={`${data.rto_in_transit_count.toLocaleString()} shipment(s), still with courier`}
            />
            <StatTile
              label="RTO Awaiting Verification"
              value={data.rto_awaiting_verification_boxes.toLocaleString()}
              icon={ShieldQuestion}
              accent="orange"
              subtext={`${data.rto_awaiting_verification_count.toLocaleString()} shipment(s), delivered, unverified`}
            />
            <StatTile
              label="Inward (selected range)"
              value={data.inward_boxes_in_range.toLocaleString()}
              icon={ArrowDownToLine}
              accent="emerald"
            />
            <StatTile
              label="Outward (selected range)"
              value={data.outward_boxes_in_range.toLocaleString()}
              icon={ArrowUpFromLine}
              accent="violet"
            />
            <StatTile
              label="Low Stock"
              value={data.low_stock_count.toLocaleString()}
              icon={AlertTriangle}
              accent="amber"
            />
            <StatTile
              label="Out of Stock"
              value={data.out_of_stock_count.toLocaleString()}
              icon={PackageX}
              accent="amber"
            />
          </div>
        )}
      </QueryStates>
    </div>
  )
}

"use client"

import * as React from "react"
import { useRouter } from "next/navigation"
import {
  AlertCircle,
  AlertTriangle,
  CheckCircle2,
  ClipboardList,
  CreditCard,
  PackageCheck,
  PackageX,
  RotateCcw,
  Timer,
  Truck,
  Undo2,
  XCircle,
} from "lucide-react"

import { PageHeader } from "@/components/shared/page-header"
import { StatTile } from "@/components/shared/stat-tile"
import { ShipmentDailyTrendChart } from "@/components/shipments/shipment-daily-trend-chart"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { getApiErrorMessage } from "@/lib/api-client"
import { useShipmentAnalytics, useShipmentSummary } from "@/services/shipment-queue"
import { useShipmentStaffPerformance } from "@/services/shipment-staff"
import type { ShipmentAnalytics, ShipmentSummary } from "@/types/shipment"

interface QueryLike<T> {
  data: T | undefined
  isLoading: boolean
  isError: boolean
  error: unknown
}

interface FulfillmentDashboardProps {
  /** Swap in a SCOPED pair of hooks (e.g. `useMyShipmentSummary`/
   * `useMyShipmentAnalytics`) to reuse this exact dashboard body for the
   * Shipment Staff experience — every existing caller (`/fulfillment/
   * dashboard`, the legacy `/shipment-dashboard` alias) omits these and
   * gets the original org-wide hooks, completely unaffected.
   */
  useSummary?: () => QueryLike<ShipmentSummary>
  useAnalytics?: () => QueryLike<ShipmentAnalytics>
  title?: string
  queueHref?: string
  /** The telecaller-wise table normally drills into the Admin-only
   * `/team/telecallers/{id}` page -- not reachable by a Shipment Staff
   * user, so their dashboard passes `false` here instead of linking
   * somewhere that would just 403/redirect.
   */
  telecallerRowsClickable?: boolean
  /** The Admin-only "Shipment Staff Performance" table (Part 7) — every
   * Shipment Staff user's assigned-Telecaller count and aggregated
   * confirm/ship/deliver/NDR/RTO figures across their own scope. Off by
   * default on a Shipment Staff user's own dashboard (that's other
   * people's performance, not their own scope).
   */
  showShipmentStaffPerformance?: boolean
}

const STATUS_LABELS: Record<string, string> = {
  pending: "Pending",
  picked_up: "Picked Up",
  in_transit: "In Transit",
  out_for_delivery: "Out for Delivery",
  delivered: "Delivered",
  ndr: "NDR",
  rto_initiated: "RTO Initiated",
  rto_delivered: "RTO Delivered",
  cancelled: "Cancelled",
}

/** The Fulfillment dashboard body. Rendered both at `/fulfillment/dashboard`
 * (the dedicated Fulfillment module) and at the legacy `/shipment-dashboard`
 * route, which is kept as a thin alias so existing bookmarks keep working.
 * Every figure is a real backend aggregate — see `ShipmentService.get_summary`
 * / `get_analytics`.
 */
export function FulfillmentDashboard(props: FulfillmentDashboardProps = {}) {
  return (
    <React.Suspense>
      <FulfillmentDashboardContent {...props} />
    </React.Suspense>
  )
}

function FulfillmentDashboardContent({
  useSummary = useShipmentSummary,
  useAnalytics = useShipmentAnalytics,
  title = "Fulfillment Dashboard",
  queueHref = "/fulfillment/orders",
  telecallerRowsClickable = true,
  showShipmentStaffPerformance = true,
}: FulfillmentDashboardProps) {
  const router = useRouter()
  const summaryQuery = useSummary()
  const analyticsQuery = useAnalytics()
  const summary = summaryQuery.data
  const analytics = analyticsQuery.data
  const staffPerformanceQuery = useShipmentStaffPerformance(showShipmentStaffPerformance)

  // A failed request must never read as "zero shipments" -- the stat
  // tiles/cards below already fall back to "--"/empty copy when data is
  // undefined, which is indistinguishable from a genuinely empty org unless
  // this banner calls out that the fetch itself failed.
  const loadError = summaryQuery.isError
    ? summaryQuery.error
    : analyticsQuery.isError
      ? analyticsQuery.error
      : undefined

  return (
    <>
      <PageHeader
        title={title}
        description="Confirmed orders, shipment pipeline, and delivery performance."
      />

      {loadError !== undefined && (
        <Alert variant="destructive" className="mb-6">
          <AlertCircle className="size-4" />
          <AlertTitle>Could not load the fulfillment dashboard</AlertTitle>
          <AlertDescription>{getApiErrorMessage(loadError)}</AlertDescription>
        </Alert>
      )}

      <div className="mb-6 grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-5">
        <StatTile
          label="Confirmed, Awaiting Shipment"
          value={summary?.confirmed_awaiting_shipment ?? "—"}
          icon={ClipboardList}
          accent="amber"
          href={queueHref}
        />
        <StatTile
          label="Total Shipments"
          value={summary?.total_shipments ?? "—"}
          icon={Truck}
          accent="slate"
        />
        <StatTile
          label="In Transit"
          value={summary?.in_transit ?? "—"}
          icon={Timer}
          accent="blue"
        />
        <StatTile
          label="Out for Delivery"
          value={summary?.out_for_delivery ?? "—"}
          icon={PackageCheck}
          accent="violet"
        />
        <StatTile
          label="Delivered"
          value={summary?.delivered ?? "—"}
          icon={CheckCircle2}
          accent="emerald"
        />
        <StatTile label="NDR" value={summary?.ndr ?? "—"} icon={AlertTriangle} accent="orange" />
        <StatTile label="RTO" value={summary?.rto ?? "—"} icon={Undo2} accent="orange" />
        <StatTile
          label="Cancelled"
          value={summary?.cancelled ?? "—"}
          icon={XCircle}
          accent="orange"
        />
        <StatTile
          label="COD Shipments"
          value={summary?.cod ?? "—"}
          icon={PackageX}
          accent="slate"
        />
        <StatTile
          label="Prepaid Shipments"
          value={summary?.prepaid ?? "—"}
          icon={CreditCard}
          accent="blue"
        />
        <StatTile
          label="Today's Shipments"
          value={summary?.todays_shipments ?? "—"}
          icon={RotateCcw}
          accent="blue"
        />
      </div>

      <div className="mb-6 grid gap-4 lg:grid-cols-2">
        <ShipmentDailyTrendChart data={analytics?.daily_trend} isLoading={analyticsQuery.isLoading} />

        <Card>
          <CardHeader>
            <CardTitle>Shipment Status Breakdown</CardTitle>
          </CardHeader>
          <CardContent>
            {analyticsQuery.isLoading ? (
              <div className="bg-muted h-[220px] w-full animate-pulse rounded-md" />
            ) : !analytics || analytics.status_breakdown.length === 0 ? (
              <div className="text-muted-foreground flex h-[220px] items-center justify-center text-sm">
                No shipments yet.
              </div>
            ) : (
              <div className="flex flex-col gap-2">
                {analytics.status_breakdown.map((row) => (
                  <div key={row.status} className="flex items-center justify-between text-sm">
                    <span className="text-muted-foreground">
                      {STATUS_LABELS[row.status] ?? row.status}
                    </span>
                    <span className="font-medium tabular-nums">{row.count}</span>
                  </div>
                ))}
                <div className="border-border mt-2 flex items-center justify-between border-t pt-2 text-sm font-semibold">
                  <span>Confirmation → Shipment Rate</span>
                  <span className="tabular-nums">
                    {analytics.confirmation_to_shipment_rate}%
                  </span>
                </div>
              </div>
            )}
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Telecaller-wise Confirmations & Shipments</CardTitle>
        </CardHeader>
        <CardContent>
          {analyticsQuery.isLoading ? (
            <div className="bg-muted h-24 w-full animate-pulse rounded-md" />
          ) : !analytics || analytics.telecaller_stats.length === 0 ? (
            <p className="text-muted-foreground text-sm">
              No telecaller-confirmed orders yet.
            </p>
          ) : (
            <div className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Telecaller</TableHead>
                    <TableHead className="text-right">Confirmed</TableHead>
                    <TableHead className="text-right">Shipped</TableHead>
                    <TableHead className="text-right">Delivered</TableHead>
                    <TableHead className="text-right">NDR</TableHead>
                    <TableHead className="text-right">RTO</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {analytics.telecaller_stats.map((row) => (
                    <TableRow
                      key={row.telecaller_id}
                      className={
                        telecallerRowsClickable ? "hover:bg-accent/60 cursor-pointer" : undefined
                      }
                      onClick={
                        telecallerRowsClickable
                          ? () => router.push(`/team/telecallers/${row.telecaller_id}`)
                          : undefined
                      }
                    >
                      <TableCell className="font-medium">{row.telecaller_name}</TableCell>
                      <TableCell className="text-right tabular-nums">{row.confirmed}</TableCell>
                      <TableCell className="text-right tabular-nums">{row.shipped}</TableCell>
                      <TableCell className="text-right tabular-nums">{row.delivered}</TableCell>
                      <TableCell className="text-right tabular-nums">{row.ndr}</TableCell>
                      <TableCell className="text-right tabular-nums">{row.rto}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}
        </CardContent>
      </Card>

      {showShipmentStaffPerformance && (
        <Card className="mt-6">
          <CardHeader>
            <CardTitle>Shipment Staff Performance</CardTitle>
          </CardHeader>
          <CardContent>
            {staffPerformanceQuery.isLoading ? (
              <div className="bg-muted h-24 w-full animate-pulse rounded-md" />
            ) : !staffPerformanceQuery.data || staffPerformanceQuery.data.length === 0 ? (
              <p className="text-muted-foreground text-sm">
                No Shipment Staff users yet — create one under Administration → Users.
              </p>
            ) : (
              <div className="overflow-x-auto">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Shipment Staff</TableHead>
                      <TableHead className="text-right">Telecallers</TableHead>
                      <TableHead className="text-right">Confirmed</TableHead>
                      <TableHead className="text-right">Shipped</TableHead>
                      <TableHead className="text-right">Delivered</TableHead>
                      <TableHead className="text-right">NDR</TableHead>
                      <TableHead className="text-right">RTO</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {staffPerformanceQuery.data.map((row) => (
                      <TableRow key={row.shipment_staff_id}>
                        <TableCell className="font-medium">{row.shipment_staff_name}</TableCell>
                        <TableCell className="text-right tabular-nums">
                          {row.telecaller_count}
                        </TableCell>
                        <TableCell className="text-right tabular-nums">{row.confirmed}</TableCell>
                        <TableCell className="text-right tabular-nums">{row.shipped}</TableCell>
                        <TableCell className="text-right tabular-nums">{row.delivered}</TableCell>
                        <TableCell className="text-right tabular-nums">{row.ndr}</TableCell>
                        <TableCell className="text-right tabular-nums">{row.rto}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            )}
          </CardContent>
        </Card>
      )}
    </>
  )
}

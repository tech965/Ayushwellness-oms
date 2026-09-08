"use client"

import * as React from "react"
import { useRouter } from "next/navigation"
import {
  AlertTriangle,
  CheckCircle2,
  ClipboardList,
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
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { useShipmentAnalytics, useShipmentSummary } from "@/services/shipment-queue"

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

export default function ShipmentDashboardPage() {
  return (
    <React.Suspense>
      <ShipmentDashboardContent />
    </React.Suspense>
  )
}

function ShipmentDashboardContent() {
  const router = useRouter()
  const summaryQuery = useShipmentSummary()
  const analyticsQuery = useShipmentAnalytics()
  const summary = summaryQuery.data
  const analytics = analyticsQuery.data

  return (
    <>
      <PageHeader
        title="Shipment Dashboard"
        description="Confirmed orders, shipment pipeline, and delivery performance."
      />

      <div className="mb-6 grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-5">
        <StatTile
          label="Confirmed, Awaiting Shipment"
          value={summary?.confirmed_awaiting_shipment ?? "—"}
          icon={ClipboardList}
          accent="amber"
          href="/shipment-queue"
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
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {analytics.telecaller_stats.map((row) => (
                    <TableRow
                      key={row.telecaller_id}
                      className="hover:bg-accent/60 cursor-pointer"
                      onClick={() => router.push(`/team/telecallers/${row.telecaller_id}`)}
                    >
                      <TableCell className="font-medium">{row.telecaller_name}</TableCell>
                      <TableCell className="text-right tabular-nums">{row.confirmed}</TableCell>
                      <TableCell className="text-right tabular-nums">{row.shipped}</TableCell>
                      <TableCell className="text-right tabular-nums">{row.delivered}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}
        </CardContent>
      </Card>
    </>
  )
}

"use client"

import {
  Bar,
  CartesianGrid,
  ComposedChart,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts"
import { format, parseISO } from "date-fns"

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import type { DailyShipmentTrendPoint } from "@/types/shipment"

interface ShipmentDailyTrendChartProps {
  data: DailyShipmentTrendPoint[] | undefined
  isLoading: boolean
}

function bucketLabel(date: string): string {
  try {
    return format(parseISO(date), "d MMM")
  } catch {
    return date
  }
}

/** Shipments created (bar) vs delivered (line) per day — both from real,
 * already-existing timestamps (`Shipment.created_at`/
 * `Shipment.actual_delivery_date`), same visual language as
 * `OrdersRevenueChart`/`TelecallerDailyPerformanceChart`.
 */
export function ShipmentDailyTrendChart({ data, isLoading }: ShipmentDailyTrendChartProps) {
  const points = data ?? []
  const chartData = points.map((p) => ({
    date: bucketLabel(p.date),
    created: p.created,
    delivered: p.delivered,
  }))

  return (
    <Card>
      <CardHeader>
        <CardTitle>Daily Shipment Trend</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="text-muted-foreground mb-3 flex items-center gap-4 text-xs">
          <span className="flex items-center gap-1.5">
            <span className="size-2.5 rounded-sm" style={{ backgroundColor: "var(--chart-1)" }} />
            Created
          </span>
          <span className="flex items-center gap-1.5">
            <span className="size-2.5 rounded-full" style={{ backgroundColor: "var(--chart-2)" }} />
            Delivered
          </span>
        </div>
        {isLoading ? (
          <div className="bg-muted h-[280px] w-full animate-pulse rounded-md" />
        ) : chartData.length === 0 ? (
          <div className="text-muted-foreground flex h-[280px] items-center justify-center text-sm">
            No shipment activity in the selected range.
          </div>
        ) : (
          <ResponsiveContainer width="100%" height={280}>
            <ComposedChart data={chartData} margin={{ left: 0, right: 8, top: 8, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
              <XAxis
                dataKey="date"
                tick={{ fontSize: 12, fill: "var(--muted-foreground)" }}
                axisLine={{ stroke: "var(--border)" }}
                tickLine={false}
              />
              <YAxis
                tick={{ fontSize: 12, fill: "var(--muted-foreground)" }}
                axisLine={false}
                tickLine={false}
                width={32}
                allowDecimals={false}
              />
              <Tooltip
                cursor={{ fill: "var(--muted)", opacity: 0.5 }}
                contentStyle={{
                  backgroundColor: "var(--popover)",
                  color: "var(--popover-foreground)",
                  border: "1px solid var(--border)",
                  borderRadius: "var(--radius-md)",
                  boxShadow: "var(--shadow-soft)",
                  fontSize: 12,
                  padding: "8px 12px",
                }}
                labelStyle={{ color: "var(--foreground)", fontWeight: 600, marginBottom: 4 }}
                formatter={(value, name) => [
                  Number(value).toLocaleString("en-IN"),
                  name === "created" ? "Created" : "Delivered",
                ]}
              />
              <Bar dataKey="created" fill="var(--chart-1)" radius={[4, 4, 0, 0]} maxBarSize={28} />
              <Line
                dataKey="delivered"
                type="monotone"
                stroke="var(--chart-2)"
                strokeWidth={2.5}
                dot={false}
                activeDot={{ r: 5, stroke: "var(--card)", strokeWidth: 2 }}
              />
            </ComposedChart>
          </ResponsiveContainer>
        )}
      </CardContent>
    </Card>
  )
}

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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { formatMoney } from "@/lib/format"
import type { OrdersTimeseries, TimeseriesInterval } from "@/types/analytics"

interface OrdersRevenueChartProps {
  data: OrdersTimeseries | undefined
  interval: TimeseriesInterval
  onIntervalChange: (interval: TimeseriesInterval) => void
  isLoading: boolean
}

function bucketLabel(bucket: string): string {
  try {
    return format(parseISO(bucket), "d MMM")
  } catch {
    return bucket
  }
}

export function OrdersRevenueChart({
  data,
  interval,
  onIntervalChange,
  isLoading,
}: OrdersRevenueChartProps) {
  const points = data?.points ?? []
  const chartData = points.map((p) => ({
    bucket: bucketLabel(p.bucket),
    orders: p.order_count,
    revenue: Number(p.revenue),
  }))

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between gap-2">
        <CardTitle>Orders &amp; Revenue</CardTitle>
        <Select
          value={interval}
          onValueChange={(v) => onIntervalChange(v as TimeseriesInterval)}
        >
          <SelectTrigger className="w-[110px]" size="sm">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="day">Daily</SelectItem>
            <SelectItem value="week">Weekly</SelectItem>
            <SelectItem value="month">Monthly</SelectItem>
          </SelectContent>
        </Select>
      </CardHeader>
      <CardContent>
        <div className="text-muted-foreground mb-3 flex items-center gap-4 text-xs">
          <span className="flex items-center gap-1.5">
            <span
              className="size-2.5 rounded-sm"
              style={{ backgroundColor: "var(--chart-1)" }}
            />
            Orders
          </span>
          <span className="flex items-center gap-1.5">
            <span
              className="size-2.5 rounded-full"
              style={{ backgroundColor: "var(--chart-2)" }}
            />
            Revenue (₹)
          </span>
        </div>
        {isLoading ? (
          <div className="bg-muted h-[300px] w-full animate-pulse rounded-md" />
        ) : chartData.length === 0 ? (
          <div className="text-muted-foreground flex h-[300px] items-center justify-center text-sm">
            No orders in the selected range.
          </div>
        ) : (
          <ResponsiveContainer width="100%" height={300}>
            <ComposedChart
              data={chartData}
              margin={{ left: 0, right: 8, top: 8, bottom: 0 }}
            >
              <CartesianGrid
                strokeDasharray="3 3"
                stroke="var(--border)"
                vertical={false}
              />
              <XAxis
                dataKey="bucket"
                tick={{ fontSize: 12, fill: "var(--muted-foreground)" }}
                axisLine={{ stroke: "var(--border)" }}
                tickLine={false}
              />
              <YAxis
                yAxisId="orders"
                tick={{ fontSize: 12, fill: "var(--muted-foreground)" }}
                axisLine={false}
                tickLine={false}
                width={36}
                allowDecimals={false}
              />
              <YAxis
                yAxisId="revenue"
                orientation="right"
                tick={{ fontSize: 12, fill: "var(--muted-foreground)" }}
                axisLine={false}
                tickLine={false}
                width={56}
                tickFormatter={(value: number) => formatMoney(value).replace(".00", "")}
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
                labelStyle={{
                  color: "var(--foreground)",
                  fontWeight: 600,
                  marginBottom: 4,
                }}
                formatter={(value, name) =>
                  name === "revenue"
                    ? [formatMoney(Number(value)), "Revenue"]
                    : [Number(value).toLocaleString("en-IN"), "Orders"]
                }
              />
              <Bar
                yAxisId="orders"
                dataKey="orders"
                fill="var(--chart-1)"
                radius={[4, 4, 0, 0]}
                maxBarSize={28}
              />
              <Line
                yAxisId="revenue"
                dataKey="revenue"
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

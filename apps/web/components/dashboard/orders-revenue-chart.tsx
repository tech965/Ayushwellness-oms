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

/** Today-vs-Yesterday comparison data — only meaningful when `interval`
 * is "hour" (a single IST calendar day has nothing useful to show at
 * day/week/month granularity). `currentIstHour` bounds how much of
 * "today" has actually elapsed, so hours that haven't happened yet are
 * left blank rather than rendered as a fake "0 orders."
 */
export interface HourlyComparison {
  yesterday: OrdersTimeseries | undefined
  isLoading: boolean
  currentIstHour: number
}

interface OrdersRevenueChartProps {
  data: OrdersTimeseries | undefined
  interval: TimeseriesInterval
  onIntervalChange: (interval: TimeseriesInterval) => void
  isLoading: boolean
  comparison?: HourlyComparison
}

function bucketLabel(bucket: string): string {
  try {
    return format(parseISO(bucket), "d MMM")
  } catch {
    return bucket
  }
}

// Hour buckets from the backend are "YYYY-MM-DDTHH:00" (IST hour) --
// slicing out the HH avoids re-parsing as a Date (which would reinterpret
// the string in the browser's local timezone).
function hourFromBucket(bucket: string): number {
  return Number(bucket.slice(11, 13))
}

function hourLabel(hour: number): string {
  const period = hour < 12 ? "AM" : "PM"
  const displayHour = hour % 12 === 0 ? 12 : hour % 12
  return `${displayHour} ${period}`
}

interface HourlyChartPoint {
  bucket: string
  orders: number | null
  revenue: number | null
  ordersYesterday: number | null
}

function buildHourlyComparisonData(
  today: OrdersTimeseries | undefined,
  yesterday: OrdersTimeseries | undefined,
  currentIstHour: number
): HourlyChartPoint[] {
  const todayByHour = new Map((today?.points ?? []).map((p) => [hourFromBucket(p.bucket), p]))
  const yesterdayByHour = new Map(
    (yesterday?.points ?? []).map((p) => [hourFromBucket(p.bucket), p])
  )
  return Array.from({ length: 24 }, (_, hour) => {
    const todayPoint = todayByHour.get(hour)
    const yesterdayPoint = yesterdayByHour.get(hour)
    const hasElapsed = hour <= currentIstHour
    return {
      bucket: hourLabel(hour),
      orders: hasElapsed ? (todayPoint?.order_count ?? 0) : null,
      revenue: hasElapsed ? Number(todayPoint?.revenue ?? 0) : null,
      ordersYesterday: yesterdayPoint?.order_count ?? 0,
    }
  })
}

export function OrdersRevenueChart({
  data,
  interval,
  onIntervalChange,
  isLoading,
  comparison,
}: OrdersRevenueChartProps) {
  const isComparisonMode = interval === "hour" && comparison !== undefined
  const points = data?.points ?? []
  const chartData = isComparisonMode
    ? buildHourlyComparisonData(data, comparison.yesterday, comparison.currentIstHour)
    : points.map((p) => ({
        bucket: bucketLabel(p.bucket),
        orders: p.order_count,
        revenue: Number(p.revenue),
        ordersYesterday: null,
      }))
  const chartIsLoading = isLoading || (isComparisonMode && comparison.isLoading)

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between gap-2">
        <CardTitle>
          {isComparisonMode ? "Today vs. Yesterday" : "Orders & Revenue"}
        </CardTitle>
        {isComparisonMode ? (
          <span className="text-muted-foreground text-xs font-medium">Hourly</span>
        ) : (
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
        )}
      </CardHeader>
      <CardContent>
        <div className="text-muted-foreground mb-3 flex items-center gap-4 text-xs">
          <span className="flex items-center gap-1.5">
            <span
              className="size-2.5 rounded-sm"
              style={{ backgroundColor: "var(--chart-1)" }}
            />
            {isComparisonMode ? "Orders (Today)" : "Orders"}
          </span>
          {isComparisonMode ? (
            <span className="flex items-center gap-1.5">
              <span
                className="size-2.5 rounded-sm"
                style={{ backgroundColor: "var(--chart-1)", opacity: 0.35 }}
              />
              Orders (Yesterday)
            </span>
          ) : null}
          <span className="flex items-center gap-1.5">
            <span
              className="size-2.5 rounded-full"
              style={{ backgroundColor: "var(--chart-2)" }}
            />
            Revenue (₹)
          </span>
        </div>
        {chartIsLoading ? (
          <div className="bg-muted h-[300px] w-full animate-pulse rounded-md" />
        ) : !isComparisonMode && chartData.length === 0 ? (
          <div className="text-muted-foreground flex h-[300px] items-center justify-center text-sm">
            No orders in the selected range.
          </div>
        ) : isComparisonMode &&
          points.length === 0 &&
          (comparison.yesterday?.points.length ?? 0) === 0 ? (
          <div className="text-muted-foreground flex h-[300px] items-center justify-center text-sm">
            No orders today or yesterday.
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
                formatter={(value, name) => {
                  if (name === "revenue") return [formatMoney(Number(value)), "Revenue"]
                  if (name === "ordersYesterday") {
                    return [Number(value).toLocaleString("en-IN"), "Orders (Yesterday)"]
                  }
                  return [
                    Number(value).toLocaleString("en-IN"),
                    isComparisonMode ? "Orders (Today)" : "Orders",
                  ]
                }}
              />
              {isComparisonMode ? (
                <Bar
                  yAxisId="orders"
                  dataKey="ordersYesterday"
                  fill="var(--chart-1)"
                  fillOpacity={0.35}
                  radius={[4, 4, 0, 0]}
                  maxBarSize={20}
                />
              ) : null}
              <Bar
                yAxisId="orders"
                dataKey="orders"
                fill="var(--chart-1)"
                radius={[4, 4, 0, 0]}
                maxBarSize={isComparisonMode ? 20 : 28}
              />
              <Line
                yAxisId="revenue"
                dataKey="revenue"
                type="monotone"
                stroke="var(--chart-2)"
                strokeWidth={2.5}
                dot={false}
                activeDot={{ r: 5, stroke: "var(--card)", strokeWidth: 2 }}
                connectNulls
              />
            </ComposedChart>
          </ResponsiveContainer>
        )}
      </CardContent>
    </Card>
  )
}

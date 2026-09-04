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

/** Selected-day-vs-previous-day comparison data — only meaningful when
 * `interval` is "hour" (a single IST calendar day has nothing useful to
 * show at day/week/month granularity). Used for both the Today (vs.
 * Yesterday) and Yesterday (vs. the day before) dashboard views — the
 * comparison is always "selected day vs. the calendar day immediately
 * before it," never a fixed pair of days. `currentIstHour` bounds how
 * much of the *primary* day has actually elapsed, so hours that haven't
 * happened yet are left blank rather than rendered as a fake "0 orders" —
 * for any day other than the real current day this is always `23` (a
 * past day is always fully elapsed).
 */
export interface HourlyComparison {
  previous: OrdersTimeseries | undefined
  isLoading: boolean
  currentIstHour: number
  /** Human-readable label for the primary series — "Today" or
   * "Yesterday". Never hardcoded in the chart itself so the legend/title/
   * tooltips always match whichever day is actually selected.
   */
  primaryLabel: string
  /** Human-readable label for the comparison series — "Yesterday" when
   * the primary day is Today, otherwise a calendar date (e.g. "1 Sep").
   */
  previousLabel: string
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
  ordersPrevious: number | null
}

/** Builds the 24-hour (12 AM - 11 PM) comparison series for the primary
 * selected day vs. the previous calendar day. Exported for direct unit
 * testing of the elapsed-hour/null-vs-zero rules, which matter more here
 * than any particular rendered pixel.
 */
export function buildHourlyComparisonData(
  primary: OrdersTimeseries | undefined,
  previous: OrdersTimeseries | undefined,
  currentIstHour: number
): HourlyChartPoint[] {
  const primaryByHour = new Map((primary?.points ?? []).map((p) => [hourFromBucket(p.bucket), p]))
  const previousByHour = new Map(
    (previous?.points ?? []).map((p) => [hourFromBucket(p.bucket), p])
  )
  return Array.from({ length: 24 }, (_, hour) => {
    const primaryPoint = primaryByHour.get(hour)
    const previousPoint = previousByHour.get(hour)
    const hasElapsed = hour <= currentIstHour
    return {
      bucket: hourLabel(hour),
      orders: hasElapsed ? (primaryPoint?.order_count ?? 0) : null,
      revenue: hasElapsed ? Number(primaryPoint?.revenue ?? 0) : null,
      ordersPrevious: previousPoint?.order_count ?? 0,
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
    ? buildHourlyComparisonData(data, comparison.previous, comparison.currentIstHour)
    : points.map((p) => ({
        bucket: bucketLabel(p.bucket),
        orders: p.order_count,
        revenue: Number(p.revenue),
        ordersPrevious: null,
      }))
  const chartIsLoading = isLoading || (isComparisonMode && comparison.isLoading)
  const primaryLabel = comparison?.primaryLabel ?? ""
  const previousLabel = comparison?.previousLabel ?? ""

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between gap-2">
        <CardTitle>
          {isComparisonMode ? `${primaryLabel} vs. ${previousLabel}` : "Orders & Revenue"}
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
            {isComparisonMode ? `Orders (${primaryLabel})` : "Orders"}
          </span>
          {isComparisonMode ? (
            <span className="flex items-center gap-1.5">
              <span
                className="size-2.5 rounded-sm"
                style={{ backgroundColor: "var(--chart-1)", opacity: 0.35 }}
              />
              {`Orders (${previousLabel})`}
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
          (comparison.previous?.points.length ?? 0) === 0 ? (
          <div className="text-muted-foreground flex h-[300px] items-center justify-center text-sm">
            {`No orders on ${primaryLabel} or ${previousLabel}.`}
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
                  if (name === "ordersPrevious") {
                    return [Number(value).toLocaleString("en-IN"), `Orders (${previousLabel})`]
                  }
                  return [
                    Number(value).toLocaleString("en-IN"),
                    isComparisonMode ? `Orders (${primaryLabel})` : "Orders",
                  ]
                }}
              />
              {isComparisonMode ? (
                <Bar
                  yAxisId="orders"
                  dataKey="ordersPrevious"
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

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
import type { TimeseriesInterval } from "@/types/analytics"
import type { CashfreePaymentTrend } from "@/types/cashfree"

interface PaymentTrendChartProps {
  data: CashfreePaymentTrend | undefined
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

/** Payment trend by day/week/month (every provider, or whichever one the
 * page's own provider filter is set to) — paid vs failed counts as
 * grouped bars, total amount received as a line on a secondary axis.
 * Same chart shape (`ComposedChart` + interval `<Select>`) as
 * `OrdersRevenueChart`. Fed by `usePaymentTrend` (provider-agnostic);
 * the type import below is still `CashfreePaymentTrend` purely because
 * it's the same response shape, reused rather than duplicated.
 */
export function PaymentTrendChart({
  data,
  interval,
  onIntervalChange,
  isLoading,
}: PaymentTrendChartProps) {
  const points = data?.points ?? []
  const chartData = points.map((p) => ({
    bucket: bucketLabel(p.bucket),
    paid: p.paid_count,
    failed: p.failed_count,
    amount: Number(p.total_amount),
  }))

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between gap-2">
        <CardTitle>Payment Trend</CardTitle>
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
            <span className="size-2.5 rounded-sm" style={{ backgroundColor: "var(--success)" }} />
            Paid
          </span>
          <span className="flex items-center gap-1.5">
            <span className="size-2.5 rounded-sm" style={{ backgroundColor: "var(--danger)" }} />
            Failed
          </span>
          <span className="flex items-center gap-1.5">
            <span
              className="size-2.5 rounded-full"
              style={{ backgroundColor: "var(--chart-2)" }}
            />
            Amount Received (₹)
          </span>
        </div>
        {isLoading ? (
          <div className="bg-muted h-[300px] w-full animate-pulse rounded-md" />
        ) : chartData.length === 0 ? (
          <div className="text-muted-foreground flex h-[300px] items-center justify-center text-sm">
            No payments in the selected range.
          </div>
        ) : (
          <ResponsiveContainer width="100%" height={300}>
            <ComposedChart data={chartData} margin={{ left: 0, right: 8, top: 8, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
              <XAxis
                dataKey="bucket"
                tick={{ fontSize: 12, fill: "var(--muted-foreground)" }}
                axisLine={{ stroke: "var(--border)" }}
                tickLine={false}
              />
              <YAxis
                yAxisId="count"
                tick={{ fontSize: 12, fill: "var(--muted-foreground)" }}
                axisLine={false}
                tickLine={false}
                width={36}
                allowDecimals={false}
              />
              <YAxis
                yAxisId="amount"
                orientation="right"
                tick={{ fontSize: 12, fill: "var(--muted-foreground)" }}
                axisLine={false}
                tickLine={false}
                width={64}
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
                labelStyle={{ color: "var(--foreground)", fontWeight: 600, marginBottom: 4 }}
                formatter={(value, name) => {
                  if (name === "amount") return [formatMoney(Number(value)), "Amount Received"]
                  return [Number(value).toLocaleString("en-IN"), name === "paid" ? "Paid" : "Failed"]
                }}
              />
              <Bar
                yAxisId="count"
                dataKey="paid"
                fill="var(--success)"
                radius={[4, 4, 0, 0]}
                maxBarSize={20}
              />
              <Bar
                yAxisId="count"
                dataKey="failed"
                fill="var(--danger)"
                radius={[4, 4, 0, 0]}
                maxBarSize={20}
              />
              <Line
                yAxisId="amount"
                dataKey="amount"
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

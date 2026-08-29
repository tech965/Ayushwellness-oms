"use client"

import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts"
import { format, parseISO } from "date-fns"

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { formatMoney } from "@/lib/format"

export interface SplitTimelinePoint {
  bucket: string
  seriesAValue: number
  seriesBValue: number
}

interface SplitTimelineChartProps {
  title: string
  data: SplitTimelinePoint[]
  seriesALabel: string
  seriesBLabel: string
  seriesAColor?: string
  seriesBColor?: string
  /** "money" formats the axis/tooltip as ₹; "count" as a plain integer. */
  valueKind: "money" | "count"
  isLoading: boolean
}

function bucketLabel(bucket: string): string {
  try {
    return format(parseISO(bucket), "d MMM")
  } catch {
    return bucket
  }
}

/** One reusable grouped-bar timeline for every two-series breakdown this
 * feature needs (COD vs Prepaid revenue, COD vs Prepaid orders, Paid vs
 * Pending revenue, Paid vs Pending orders) — same recharts primitives and
 * theming as `components/dashboard/orders-revenue-chart.tsx`, generalized
 * to two arbitrary named series instead of one fixed orders/revenue pair.
 */
export function SplitTimelineChart({
  title,
  data,
  seriesALabel,
  seriesBLabel,
  seriesAColor = "var(--chart-1)",
  seriesBColor = "var(--chart-2)",
  valueKind,
  isLoading,
}: SplitTimelineChartProps) {
  const chartData = data.map((p) => ({
    bucket: bucketLabel(p.bucket),
    seriesA: p.seriesAValue,
    seriesB: p.seriesBValue,
  }))

  const formatValue = (value: number) =>
    valueKind === "money" ? formatMoney(value).replace(".00", "") : value.toLocaleString("en-IN")

  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="text-muted-foreground mb-3 flex items-center gap-4 text-xs">
          <span className="flex items-center gap-1.5">
            <span className="size-2.5 rounded-sm" style={{ backgroundColor: seriesAColor }} />
            {seriesALabel}
          </span>
          <span className="flex items-center gap-1.5">
            <span className="size-2.5 rounded-sm" style={{ backgroundColor: seriesBColor }} />
            {seriesBLabel}
          </span>
        </div>
        {isLoading ? (
          <div className="bg-muted h-[260px] w-full animate-pulse rounded-md" />
        ) : chartData.length === 0 ? (
          <div className="text-muted-foreground flex h-[260px] items-center justify-center text-sm">
            No data in the selected range.
          </div>
        ) : (
          <ResponsiveContainer width="100%" height={260}>
            <BarChart data={chartData} margin={{ left: 0, right: 8, top: 8, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
              <XAxis
                dataKey="bucket"
                tick={{ fontSize: 12, fill: "var(--muted-foreground)" }}
                axisLine={{ stroke: "var(--border)" }}
                tickLine={false}
              />
              <YAxis
                tick={{ fontSize: 12, fill: "var(--muted-foreground)" }}
                axisLine={false}
                tickLine={false}
                width={valueKind === "money" ? 56 : 36}
                tickFormatter={formatValue}
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
                  formatValue(Number(value)),
                  name === "seriesA" ? seriesALabel : seriesBLabel,
                ]}
              />
              <Bar dataKey="seriesA" fill={seriesAColor} radius={[4, 4, 0, 0]} maxBarSize={28} />
              <Bar dataKey="seriesB" fill={seriesBColor} radius={[4, 4, 0, 0]} maxBarSize={28} />
            </BarChart>
          </ResponsiveContainer>
        )}
      </CardContent>
    </Card>
  )
}

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
import type { TelecallerDailyPerformancePoint } from "@/types/telecalling"

interface TelecallerDailyPerformanceChartProps {
  data: TelecallerDailyPerformancePoint[] | undefined
  isLoading: boolean
}

function bucketLabel(date: string): string {
  try {
    return format(parseISO(date), "d MMM")
  } catch {
    return date
  }
}

interface DailyChartPoint {
  date: string
  attempts: number
  connected: number
  confirmed: number
  notInterested: number
  cancelled: number
  followUps: number
}

const TOOLTIP_ROWS: { key: keyof DailyChartPoint; label: string; color: string }[] = [
  { key: "attempts", label: "Attempts", color: "var(--chart-1)" },
  { key: "connected", label: "Connected", color: "var(--chart-2)" },
  { key: "confirmed", label: "Confirmed", color: "var(--chart-3)" },
  { key: "notInterested", label: "Not Interested", color: "var(--muted-foreground)" },
  { key: "cancelled", label: "Cancelled", color: "var(--muted-foreground)" },
  { key: "followUps", label: "Follow-ups set", color: "var(--muted-foreground)" },
]

/** Reads the full raw data point (`payload[0].payload`) rather than
 * relying on each series' own `dataKey` being present in recharts'
 * default tooltip payload — the point always carries every field of
 * `DailyChartPoint` regardless of which ones are actually drawn as a
 * bar/line, so every metric shows up on hover even though only three are
 * plotted (see the chart component's docstring for why).
 */
function DailyPerformanceTooltip({
  active,
  payload,
  label,
}: {
  active?: boolean
  payload?: { payload: DailyChartPoint }[]
  label?: string
}) {
  if (!active || !payload?.length) return null
  const point = payload[0].payload
  return (
    <div
      className="rounded-md border px-3 py-2 text-xs"
      style={{
        backgroundColor: "var(--popover)",
        color: "var(--popover-foreground)",
        borderColor: "var(--border)",
        boxShadow: "var(--shadow-soft)",
      }}
    >
      <p className="mb-1 font-semibold" style={{ color: "var(--foreground)" }}>
        {label}
      </p>
      {TOOLTIP_ROWS.map((row) => (
        <p key={row.key} className="flex items-center justify-between gap-4">
          <span className="flex items-center gap-1.5">
            <span
              className="size-2 rounded-full"
              style={{ backgroundColor: row.color }}
            />
            {row.label}
          </span>
          <span className="tabular-nums">{point[row.key].toLocaleString("en-IN")}</span>
        </p>
      ))}
    </div>
  )
}

/** Attempts as bars (total daily call volume) with Connected/Confirmed as
 * line overlays — the same "one bar + line overlays" visual language as
 * `OrdersRevenueChart`. Not Interested/Cancelled/Follow-ups are real
 * backend-computed numbers too (see the tooltip), just not drawn as
 * their own lines — six overlapping series on one chart stops being
 * readable, which the spec explicitly asks to avoid.
 */
export function TelecallerDailyPerformanceChart({
  data,
  isLoading,
}: TelecallerDailyPerformanceChartProps) {
  const points = data ?? []
  const chartData = points.map((p) => ({
    date: bucketLabel(p.date),
    attempts: p.attempts,
    connected: p.connected,
    confirmed: p.confirmed,
    notInterested: p.not_interested,
    cancelled: p.cancelled,
    followUps: p.follow_ups,
  }))

  return (
    <Card>
      <CardHeader>
        <CardTitle>Daily Performance</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="text-muted-foreground mb-3 flex flex-wrap items-center gap-4 text-xs">
          <span className="flex items-center gap-1.5">
            <span
              className="size-2.5 rounded-sm"
              style={{ backgroundColor: "var(--chart-1)" }}
            />
            Attempts
          </span>
          <span className="flex items-center gap-1.5">
            <span
              className="size-2.5 rounded-full"
              style={{ backgroundColor: "var(--chart-2)" }}
            />
            Connected
          </span>
          <span className="flex items-center gap-1.5">
            <span
              className="size-2.5 rounded-full"
              style={{ backgroundColor: "var(--chart-3)" }}
            />
            Confirmed
          </span>
        </div>
        {isLoading ? (
          <div className="bg-muted h-[280px] w-full animate-pulse rounded-md" />
        ) : chartData.length === 0 ? (
          <div className="text-muted-foreground flex h-[280px] items-center justify-center text-sm">
            No calls logged in the selected range.
          </div>
        ) : (
          <ResponsiveContainer width="100%" height={280}>
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
                content={<DailyPerformanceTooltip />}
              />
              <Bar
                dataKey="attempts"
                fill="var(--chart-1)"
                radius={[4, 4, 0, 0]}
                maxBarSize={28}
              />
              <Line
                dataKey="connected"
                type="monotone"
                stroke="var(--chart-2)"
                strokeWidth={2.5}
                dot={false}
                activeDot={{ r: 5, stroke: "var(--card)", strokeWidth: 2 }}
              />
              <Line
                dataKey="confirmed"
                type="monotone"
                stroke="var(--chart-3)"
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

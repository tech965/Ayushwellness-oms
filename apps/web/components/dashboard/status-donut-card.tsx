"use client"

import Link from "next/link"
import { Cell, Pie, PieChart, ResponsiveContainer } from "recharts"

import { StatusBadge } from "@/components/shared/status-badge"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { getStatusTone, type StatusDomain } from "@/lib/status-styles"
import type { StatusCount } from "@/types/analytics"

const TONE_COLOR: Record<string, string> = {
  neutral: "var(--muted-foreground)",
  info: "var(--info)",
  success: "var(--success)",
  warning: "var(--warning)",
  danger: "var(--danger)",
  purple: "var(--purple)",
  orange: "var(--orange)",
}

interface StatusDonutProps {
  domain: StatusDomain
  data: StatusCount[] | undefined
  isLoading: boolean
  hrefFor: (status: string) => string
  centerLabel?: string
}

/** A compact donut + legend for one status distribution — the "Fulfillment
 * Status" / "Payment Type" / "Payment Status" dashboard visuals. Every
 * legend row stays a real drill-down `<Link>` into the matching filtered
 * Orders view, same contract as `BreakdownList`.
 */
export function StatusDonut({
  domain,
  data,
  isLoading,
  hrefFor,
  centerLabel = "Total",
}: StatusDonutProps) {
  const rows = (data ?? []).slice().sort((a, b) => b.count - a.count)
  const total = rows.reduce((sum, row) => sum + row.count, 0)

  if (isLoading) {
    return <div className="bg-muted h-40 w-full animate-pulse rounded-md" />
  }

  if (rows.length === 0) {
    return <p className="text-muted-foreground text-sm">No data in the selected range.</p>
  }

  return (
    <div className="flex flex-col items-center gap-4 sm:flex-row">
      <div className="relative size-28 shrink-0">
        <ResponsiveContainer width="100%" height="100%">
          <PieChart>
            <Pie
              data={rows}
              dataKey="count"
              nameKey="status"
              innerRadius="68%"
              outerRadius="100%"
              paddingAngle={rows.length > 1 ? 2 : 0}
              stroke="none"
              isAnimationActive={false}
            >
              {rows.map((row) => (
                <Cell
                  key={row.status}
                  fill={TONE_COLOR[getStatusTone(domain, row.status)]}
                />
              ))}
            </Pie>
          </PieChart>
        </ResponsiveContainer>
        <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
          <span className="text-lg font-semibold tabular-nums">
            {total.toLocaleString("en-IN")}
          </span>
          <span className="text-muted-foreground text-center text-[0.625rem] leading-tight">
            {centerLabel}
          </span>
        </div>
      </div>
      <div className="flex w-full min-w-0 flex-1 flex-col gap-1">
        {rows.map((row) => {
          const pct = total > 0 ? (row.count / total) * 100 : 0
          return (
            <Link
              key={row.status}
              href={hrefFor(row.status)}
              className="hover:bg-accent -mx-2 flex items-center justify-between gap-2 rounded-md px-2 py-1 transition-colors"
            >
              <StatusBadge domain={domain} status={row.status} />
              <span className="text-sm tabular-nums">
                {row.count.toLocaleString("en-IN")}{" "}
                <span className="text-muted-foreground">({pct.toFixed(1)}%)</span>
              </span>
            </Link>
          )
        })}
      </div>
    </div>
  )
}

interface StatusDonutCardProps extends StatusDonutProps {
  title: string
}

/** Single-donut card — used for Fulfillment Status. */
export function StatusDonutCard({ title, ...props }: StatusDonutCardProps) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
      </CardHeader>
      <CardContent>
        <StatusDonut {...props} />
      </CardContent>
    </Card>
  )
}

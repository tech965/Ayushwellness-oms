"use client"

import Link from "next/link"
import { Cell, Pie, PieChart, ResponsiveContainer } from "recharts"

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { formatMoney } from "@/lib/format"

export interface MoneyDonutSegment {
  key: string
  label: string
  value: number
  color: string
  href?: string
}

interface MoneyDonutProps {
  segments: MoneyDonutSegment[]
  isLoading: boolean
  centerLabel?: string
}

/** A money-formatted sibling of `StatusDonut` (`components/dashboard/
 * status-donut-card.tsx`) — that component's `count` field is always a
 * plain integer, which would render a revenue split ("₹1,25,000") as an
 * unformatted raw number. Same visual language (donut + legend, same
 * recharts primitives, clickable legend rows), just money-aware.
 */
export function MoneyDonut({ segments, isLoading, centerLabel = "Total" }: MoneyDonutProps) {
  const total = segments.reduce((sum, s) => sum + s.value, 0)

  if (isLoading) {
    return <div className="bg-muted h-40 w-full animate-pulse rounded-md" />
  }

  if (total <= 0) {
    return <p className="text-muted-foreground text-sm">No revenue in the selected range.</p>
  }

  return (
    <div className="flex flex-col items-center gap-4 sm:flex-row">
      <div className="relative size-28 shrink-0">
        <ResponsiveContainer width="100%" height="100%">
          <PieChart>
            <Pie
              data={segments}
              dataKey="value"
              nameKey="label"
              innerRadius="68%"
              outerRadius="100%"
              paddingAngle={segments.length > 1 ? 2 : 0}
              stroke="none"
              isAnimationActive={false}
            >
              {segments.map((s) => (
                <Cell key={s.key} fill={s.color} />
              ))}
            </Pie>
          </PieChart>
        </ResponsiveContainer>
        <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
          <span className="text-center text-sm leading-tight font-semibold tabular-nums">
            {formatMoney(total).replace(".00", "")}
          </span>
          <span className="text-muted-foreground text-center text-[0.625rem] leading-tight">
            {centerLabel}
          </span>
        </div>
      </div>
      <div className="flex w-full min-w-0 flex-1 flex-col gap-1">
        {segments.map((s) => {
          const pct = total > 0 ? (s.value / total) * 100 : 0
          const row = (
            <div className="flex items-center justify-between gap-2">
              <span className="flex items-center gap-1.5 text-sm">
                <span
                  className="size-2.5 shrink-0 rounded-sm"
                  style={{ backgroundColor: s.color }}
                />
                {s.label}
              </span>
              <span className="text-sm tabular-nums">
                {formatMoney(s.value).replace(".00", "")}{" "}
                <span className="text-muted-foreground">({pct.toFixed(1)}%)</span>
              </span>
            </div>
          )
          return s.href ? (
            <Link
              key={s.key}
              href={s.href}
              className="hover:bg-accent -mx-2 rounded-md px-2 py-1 transition-colors"
            >
              {row}
            </Link>
          ) : (
            <div key={s.key} className="-mx-2 rounded-md px-2 py-1">
              {row}
            </div>
          )
        })}
      </div>
    </div>
  )
}

interface MoneyDonutCardProps extends MoneyDonutProps {
  title: string
}

export function MoneyDonutCard({ title, ...props }: MoneyDonutCardProps) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
      </CardHeader>
      <CardContent>
        <MoneyDonut {...props} />
      </CardContent>
    </Card>
  )
}

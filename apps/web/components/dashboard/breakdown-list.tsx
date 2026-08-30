import Link from "next/link"

import { StatusBadge } from "@/components/shared/status-badge"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { getStatusTone, type StatusDomain } from "@/lib/status-styles"
import type { StatusCount } from "@/types/analytics"

interface BreakdownListProps {
  title: string
  domain: StatusDomain
  data: StatusCount[] | undefined
  isLoading: boolean
  /** Builds the drill-down href for a given status value — typically
   * `/orders?status=X` or `/orders?shipment_status=X`.
   */
  hrefFor: (status: string) => string
}

const BAR_TONE_BG: Record<string, string> = {
  neutral: "bg-muted-foreground/40",
  info: "bg-blue-500",
  success: "bg-lime-500",
  warning: "bg-amber-500",
  danger: "bg-red-500",
  purple: "bg-purple-500",
  orange: "bg-orange-500",
}

/** Reused for every status distribution on the dashboard (order status,
 * payment status/type, fulfillment, shipment pipeline) — same badge
 * convention as everywhere else (`lib/status-styles.ts`), each row
 * clickable through to the matching filtered Orders view.
 */
export function BreakdownList({
  title,
  domain,
  data,
  isLoading,
  hrefFor,
}: BreakdownListProps) {
  const rows = data ?? []
  const total = rows.reduce((sum, row) => sum + row.count, 0)

  return (
    <Card className="shadow-[var(--shadow-soft)]">
      <CardHeader>
        <CardTitle>{title}</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-2">
        {isLoading ? (
          <div className="bg-muted h-32 w-full animate-pulse rounded-md" />
        ) : rows.length === 0 ? (
          <p className="text-muted-foreground text-sm">No data in the selected range.</p>
        ) : (
          rows
            .sort((a, b) => b.count - a.count)
            .map((row) => {
              const pct = total > 0 ? (row.count / total) * 100 : 0
              const tone = getStatusTone(domain, row.status)
              return (
                <Link
                  key={row.status}
                  href={hrefFor(row.status)}
                  className="hover:bg-accent -mx-2 flex flex-col gap-1.5 rounded-md px-2 py-2 transition-colors"
                >
                  <div className="flex items-center justify-between text-sm">
                    <StatusBadge domain={domain} status={row.status} />
                    <span className="tabular-nums">
                      {row.count}{" "}
                      <span className="text-muted-foreground">({pct.toFixed(0)}%)</span>
                    </span>
                  </div>
                  <div className="bg-muted h-1.5 w-full overflow-hidden rounded-full">
                    <div
                      className={`h-full rounded-full transition-[width] ${BAR_TONE_BG[tone]}`}
                      style={{ width: `${pct}%` }}
                    />
                  </div>
                </Link>
              )
            })
        )}
      </CardContent>
    </Card>
  )
}

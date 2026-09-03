import Link from "next/link"

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Separator } from "@/components/ui/separator"
import { formatMoney } from "@/lib/format"
import type { ReturnsRefundsSummary } from "@/types/analytics"

interface StatRowProps {
  label: string
  value: string
}

function StatRow({ label, value }: StatRowProps) {
  return (
    <div className="flex items-center justify-between text-sm">
      <span className="text-muted-foreground">{label}</span>
      <span className="font-medium tabular-nums">{value}</span>
    </div>
  )
}

interface ReturnsRefundsCardProps {
  data: ReturnsRefundsSummary | undefined
  isLoading: boolean
}

/** Returns/Refunds section for the dashboard (spec: Phase 4) — reads from
 * `GET /analytics/returns-refunds`, which is scoped by the same
 * `Return.created_at`/`Refund.created_at` in-range convention as every
 * other dashboard KPI. `return_rate_pct` is `null` (rendered "—", never
 * a fake 0%) whenever the period has no orders to divide by.
 */
export function ReturnsRefundsCard({ data, isLoading }: ReturnsRefundsCardProps) {
  const hasData = Boolean(data && (data.returns.total_returns > 0 || data.refunds.total_refunds > 0))

  return (
    <Card>
      <CardHeader>
        <CardTitle>Returns &amp; Refunds</CardTitle>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="bg-muted h-32 w-full animate-pulse rounded-md" />
        ) : !data || !hasData ? (
          <p className="text-muted-foreground text-sm">
            No return or refund data available for this range.
          </p>
        ) : (
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="flex flex-col gap-2">
              <Link
                href="/returns"
                className="text-muted-foreground hover:text-primary mb-1 text-xs font-semibold tracking-wide uppercase"
              >
                Returns
              </Link>
              <StatRow label="Total" value={data.returns.total_returns.toLocaleString("en-IN")} />
              <StatRow
                label="Pending"
                value={data.returns.pending_returns.toLocaleString("en-IN")}
              />
              <StatRow
                label="Completed"
                value={data.returns.completed_returns.toLocaleString("en-IN")}
              />
              <StatRow
                label="Return Rate"
                value={
                  data.returns.return_rate_pct === null
                    ? "—"
                    : `${data.returns.return_rate_pct.toFixed(1)}%`
                }
              />
            </div>
            <Separator className="sm:hidden" />
            <div className="flex flex-col gap-2 sm:border-l sm:pl-4">
              <Link
                href="/refunds"
                className="text-muted-foreground hover:text-primary mb-1 text-xs font-semibold tracking-wide uppercase"
              >
                Refunds
              </Link>
              <StatRow
                label="Total Refunded"
                value={formatMoney(data.refunds.total_refund_amount)}
              />
              <StatRow label="Count" value={data.refunds.total_refunds.toLocaleString("en-IN")} />
              <StatRow
                label="Pending"
                value={data.refunds.pending_refunds.toLocaleString("en-IN")}
              />
              <StatRow
                label="Completed"
                value={data.refunds.completed_refunds.toLocaleString("en-IN")}
              />
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  )
}

import Link from "next/link"
import { ArrowDownRight, ArrowUpRight, Minus, type LucideIcon } from "lucide-react"

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { cn } from "@/lib/utils"
import type { KPIValue } from "@/types/analytics"

interface KpiCardProps {
  label: string
  icon: LucideIcon
  kpi: KPIValue | undefined
  format: (value: string) => string
  href?: string
  /** Lower is better (e.g. "Delayed Shipments") — flips the up/down
   * arrow's success/danger coloring so a rising bad metric still reads
   * red, not a false "success" green.
   */
  invert?: boolean
}

/** A single dashboard KPI tile: current value, %-change vs. the prior
 * equal-length period, and (when `href` is given) a click-through to the
 * matching filtered Orders/NDR/RTO view — the dashboard's drill-down
 * contract (spec §37/§45).
 */
export function KpiCard({ label, icon: Icon, kpi, format, href, invert }: KpiCardProps) {
  const changePct = kpi?.change_pct ?? null
  const isUp = changePct !== null && changePct > 0
  const isDown = changePct !== null && changePct < 0
  const isGood = invert ? isDown : isUp
  const isBad = invert ? isUp : isDown

  const body = (
    <Card className={cn("h-full transition-colors", href && "hover:border-primary/40 cursor-pointer")}>
      <CardHeader className="flex flex-row items-center justify-between gap-2 pb-2">
        <CardTitle className="text-muted-foreground text-sm font-medium">{label}</CardTitle>
        <Icon className="text-muted-foreground size-4" />
      </CardHeader>
      <CardContent>
        <p className="text-2xl font-semibold tabular-nums">
          {kpi ? format(kpi.current) : "—"}
        </p>
        {kpi && changePct !== null && (
          <p
            className={cn(
              "mt-1 flex items-center gap-1 text-xs font-medium",
              isGood && "text-emerald-600 dark:text-emerald-400",
              isBad && "text-red-600 dark:text-red-400",
              !isGood && !isBad && "text-muted-foreground"
            )}
          >
            {isUp && <ArrowUpRight className="size-3.5" />}
            {isDown && <ArrowDownRight className="size-3.5" />}
            {!isUp && !isDown && <Minus className="size-3.5" />}
            {Math.abs(changePct).toFixed(1)}% vs previous period
          </p>
        )}
        {kpi && changePct === null && (
          <p className="text-muted-foreground mt-1 text-xs">No prior-period data</p>
        )}
      </CardContent>
    </Card>
  )

  return href ? <Link href={href}>{body}</Link> : body
}

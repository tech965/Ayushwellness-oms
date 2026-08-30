import Link from "next/link"
import {
  ArrowDownRight,
  ArrowUpRight,
  ChevronRight,
  Minus,
  type LucideIcon,
} from "lucide-react"

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { cn } from "@/lib/utils"
import type { KPIValue } from "@/types/analytics"

export type KpiAccent = "blue" | "emerald" | "amber" | "violet" | "slate" | "orange"

const ACCENT_CLASSES: Record<KpiAccent, string> = {
  blue: "bg-blue-50 text-blue-600 dark:bg-blue-500/10 dark:text-blue-400",
  emerald: "bg-lime-50 text-lime-700 dark:bg-lime-500/10 dark:text-lime-400",
  amber: "bg-amber-50 text-amber-600 dark:bg-amber-500/10 dark:text-amber-400",
  violet: "bg-violet-50 text-violet-600 dark:bg-violet-500/10 dark:text-violet-400",
  slate: "bg-slate-100 text-slate-600 dark:bg-slate-500/10 dark:text-slate-400",
  orange: "bg-orange-50 text-orange-600 dark:bg-orange-500/10 dark:text-orange-400",
}

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
  /** Tints the icon chip so KPI rows aren't visually identical tile-to-tile. */
  accent?: KpiAccent
}

/** A single dashboard KPI tile: current value, %-change vs. the prior
 * equal-length period, and (when `href` is given) a click-through to the
 * matching filtered Orders/NDR/RTO view — the dashboard's drill-down
 * contract (spec §37/§45).
 */
export function KpiCard({
  label,
  icon: Icon,
  kpi,
  format,
  href,
  invert,
  accent = "slate",
}: KpiCardProps) {
  const changePct = kpi?.change_pct ?? null
  const isUp = changePct !== null && changePct > 0
  const isDown = changePct !== null && changePct < 0
  const isGood = invert ? isDown : isUp
  const isBad = invert ? isUp : isDown

  const body = (
    <Card
      className={cn(
        "group h-full transition-all",
        href &&
          "hover:border-primary/40 cursor-pointer hover:shadow-md dark:hover:shadow-none"
      )}
    >
      <CardHeader className="flex flex-row items-center justify-between gap-2 pb-2">
        <CardTitle className="text-muted-foreground text-sm font-medium">
          {label}
        </CardTitle>
        <span
          className={cn(
            "flex size-8 shrink-0 items-center justify-center rounded-lg",
            ACCENT_CLASSES[accent]
          )}
        >
          <Icon className="size-4" />
        </span>
      </CardHeader>
      <CardContent>
        <div className="flex items-end justify-between gap-2">
          <p className="text-2xl font-semibold tabular-nums">
            {kpi ? format(kpi.current) : "—"}
          </p>
          {href && (
            <ChevronRight className="text-muted-foreground/50 group-hover:text-primary mb-1 size-4 shrink-0 transition-transform group-hover:translate-x-0.5" />
          )}
        </div>
        {kpi && changePct !== null && (
          <p
            className={cn(
              "mt-1 flex items-center gap-1 text-xs font-medium",
              isGood && "text-success",
              isBad && "text-danger",
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

  return href ? (
    <Link href={href} aria-label={`${label}: view filtered orders`}>
      {body}
    </Link>
  ) : (
    body
  )
}

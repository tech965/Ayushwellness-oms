import type { LucideIcon } from "lucide-react"
import Link from "next/link"

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { cn } from "@/lib/utils"

const ACCENT_CLASSES: Record<string, string> = {
  blue: "bg-blue-50 text-blue-600 dark:bg-blue-500/10 dark:text-blue-400",
  emerald: "bg-emerald-50 text-emerald-600 dark:bg-emerald-500/10 dark:text-emerald-400",
  amber: "bg-amber-50 text-amber-600 dark:bg-amber-500/10 dark:text-amber-400",
  violet: "bg-violet-50 text-violet-600 dark:bg-violet-500/10 dark:text-violet-400",
  orange: "bg-orange-50 text-orange-600 dark:bg-orange-500/10 dark:text-orange-400",
  slate: "bg-slate-100 text-slate-600 dark:bg-slate-500/10 dark:text-slate-400",
}

interface StatTileProps {
  label: string
  value: number | string
  icon: LucideIcon
  accent?: keyof typeof ACCENT_CLASSES
  /** A second line under the value — e.g. "23%" or a formatted money
   * amount — for tiles that need to show more than one number.
   */
  subtext?: string
  /** Makes the tile a drill-down link (e.g. into a filtered Orders view),
   * matching `KpiCard`'s click-through convention.
   */
  href?: string
}

/** A plain (non-trend) count tile — `KpiCard`'s sibling for dashboards
 * whose numbers are simple current-period counts with no prior-period
 * comparison (Telecaller/Team Leader summaries, the Order Breakdown
 * page), so it doesn't force a fake `KPIValue` shape just to reuse
 * `KpiCard`.
 */
export function StatTile({
  label,
  value,
  icon: Icon,
  accent = "slate",
  subtext,
  href,
}: StatTileProps) {
  const body = (
    <Card className={cn("h-full", href && "hover:border-primary/40 transition-colors")}>
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
        <p className="text-2xl font-semibold tabular-nums">{value}</p>
        {subtext && <p className="text-muted-foreground mt-1 text-xs">{subtext}</p>}
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

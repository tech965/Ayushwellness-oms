import Link from "next/link"
import type { LucideIcon } from "lucide-react"

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import type { StatusTone } from "@/lib/status-styles"
import { cn } from "@/lib/utils"
import type { KPIValue } from "@/types/analytics"

const TONE_CLASSES: Record<StatusTone, string> = {
  neutral: "bg-muted text-muted-foreground",
  info: "bg-blue-50 text-blue-600 dark:bg-blue-500/10 dark:text-blue-400",
  success: "bg-emerald-50 text-emerald-600 dark:bg-emerald-500/10 dark:text-emerald-400",
  warning: "bg-amber-50 text-amber-600 dark:bg-amber-500/10 dark:text-amber-400",
  danger: "bg-red-50 text-red-600 dark:bg-red-500/10 dark:text-red-400",
  purple: "bg-purple-50 text-purple-600 dark:bg-purple-500/10 dark:text-purple-400",
  orange: "bg-orange-50 text-orange-600 dark:bg-orange-500/10 dark:text-orange-400",
}

export interface ShipmentOverviewItem {
  key: string
  label: string
  icon: LucideIcon
  kpi: KPIValue | undefined
  tone: StatusTone
  href: string
}

interface ShipmentOverviewStripProps {
  items: ShipmentOverviewItem[]
  isLoading: boolean
}

/** Horizontal shipment-pipeline stat strip (Delivered / In Transit / Out for
 * Delivery / Delayed / NDR / RTO) — each tile is still a real `<Link>` to
 * the matching filtered view, preserving the existing drill-down hrefs that
 * used to live on the KPI grid.
 */
export function ShipmentOverviewStrip({ items, isLoading }: ShipmentOverviewStripProps) {
  const total = items.reduce((sum, item) => sum + Number(item.kpi?.current ?? 0), 0)

  return (
    <Card>
      <CardHeader>
        <CardTitle>Shipment Overview</CardTitle>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="bg-muted h-20 w-full animate-pulse rounded-md" />
        ) : (
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
            {items.map((item) => {
              const value = Number(item.kpi?.current ?? 0)
              const pct = total > 0 ? (value / total) * 100 : 0
              const Icon = item.icon
              return (
                <Link
                  key={item.key}
                  href={item.href}
                  className="hover:border-primary/40 hover:bg-accent/40 group flex flex-col gap-2 rounded-lg border border-transparent p-2 transition-colors"
                >
                  <span
                    className={cn(
                      "flex size-8 items-center justify-center rounded-lg",
                      TONE_CLASSES[item.tone]
                    )}
                  >
                    <Icon className="size-4" />
                  </span>
                  <span className="text-xl font-semibold tabular-nums">
                    {value.toLocaleString("en-IN")}
                  </span>
                  <span className="text-muted-foreground -mt-1.5 text-xs font-medium">
                    {item.label}
                  </span>
                  <span className="text-muted-foreground/80 text-[0.6875rem]">
                    {pct.toFixed(1)}%
                  </span>
                </Link>
              )
            })}
          </div>
        )}
      </CardContent>
    </Card>
  )
}

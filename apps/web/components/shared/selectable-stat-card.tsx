import type { ReactNode } from "react"

import { Card, CardContent } from "@/components/ui/card"
import { cn } from "@/lib/utils"

interface SelectableStatCardProps {
  label: string
  value: ReactNode
  /** One short, plain-language line explaining the number. */
  description?: string
  /** A second line such as "based on 1,204 orders". */
  detail?: string
  /** Full definition, shown on hover. */
  tooltip?: string
  selected?: boolean
  onClick: () => void
  /** Border/value accent so related cards read as a family. */
  accent?: "blue" | "green" | "orange" | "violet" | "amber" | "red" | "slate"
  className?: string
}

const ACCENT: Record<NonNullable<SelectableStatCardProps["accent"]>, string> = {
  blue: "border-l-blue-500",
  green: "border-l-lime-600",
  orange: "border-l-orange-500",
  violet: "border-l-violet-500",
  amber: "border-l-amber-500",
  red: "border-l-red-500",
  slate: "border-l-slate-400",
}

/** A KPI card that opens the matching order list when clicked. A real
 * `<button>` (keyboard + screen-reader friendly) with `aria-pressed` and a
 * visible ring for the selected state.
 */
export function SelectableStatCard({
  label,
  value,
  description,
  detail,
  tooltip,
  selected = false,
  onClick,
  accent = "blue",
  className,
}: SelectableStatCardProps) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={tooltip}
      aria-pressed={selected}
      className={cn(
        "focus-visible:ring-ring rounded-xl text-left transition focus-visible:ring-2 focus-visible:outline-none",
        className
      )}
    >
      <Card
        className={cn(
          "hover:border-primary/50 h-full border-l-4 transition-colors",
          ACCENT[accent],
          selected && "border-primary ring-primary/40 bg-primary/5 ring-2"
        )}
      >
        <CardContent className="space-y-1">
          <p className="text-muted-foreground text-sm font-medium">{label}</p>
          <p className="text-3xl font-semibold tabular-nums">{value}</p>
          {detail && <p className="text-foreground/80 text-xs font-medium">{detail}</p>}
          {description && <p className="text-muted-foreground text-xs">{description}</p>}
        </CardContent>
      </Card>
    </button>
  )
}

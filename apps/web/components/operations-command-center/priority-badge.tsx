import { Badge } from "@/components/ui/badge"
import type { Priority } from "@/types/operations-command-center"

const PRIORITY_META: Record<Priority, { label: string; emoji: string; className: string }> = {
  critical: {
    label: "Critical",
    emoji: "🔴",
    className: "bg-red-50 text-red-700 dark:bg-red-500/10 dark:text-red-400",
  },
  warning: {
    label: "Warning",
    emoji: "🟠",
    className: "bg-orange-50 text-orange-700 dark:bg-orange-500/10 dark:text-orange-400",
  },
  opportunity: {
    label: "Opportunity",
    emoji: "🟡",
    className: "bg-amber-50 text-amber-700 dark:bg-amber-500/10 dark:text-amber-400",
  },
  positive: {
    label: "Positive",
    emoji: "🟢",
    className: "bg-emerald-50 text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-400",
  },
}

export function PriorityBadge({ value }: { value: Priority }) {
  const meta = PRIORITY_META[value]
  return (
    <Badge variant="outline" className={meta.className}>
      {meta.emoji} {meta.label}
    </Badge>
  )
}

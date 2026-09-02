import { Badge } from "@/components/ui/badge"
import type { OpportunityBucket } from "@/types/supply-intelligence"

// Deterministic mapping to `SupplyIntelligenceService._classify_opportunity`'s
// four headline buckets plus the neutral "steady" fallback -- see that
// function's docstring for the exact thresholds.
const OPPORTUNITY_META: Record<
  OpportunityBucket,
  { label: string; emoji: string; className: string }
> = {
  scale: {
    label: "Scale",
    emoji: "🟢",
    className: "bg-emerald-50 text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-400",
  },
  opportunity: {
    label: "Opportunity",
    emoji: "🟡",
    className: "bg-amber-50 text-amber-700 dark:bg-amber-500/10 dark:text-amber-400",
  },
  investigate: {
    label: "Investigate",
    emoji: "🔴",
    className: "bg-red-50 text-red-700 dark:bg-red-500/10 dark:text-red-400",
  },
  untapped: {
    label: "Untapped",
    emoji: "⚪",
    className: "bg-slate-100 text-slate-600 dark:bg-slate-500/10 dark:text-slate-400",
  },
  steady: {
    label: "Steady",
    emoji: "🔵",
    className: "bg-blue-50 text-blue-700 dark:bg-blue-500/10 dark:text-blue-400",
  },
}

export function OpportunityBadge({ value }: { value: OpportunityBucket }) {
  const meta = OPPORTUNITY_META[value]
  return (
    <Badge variant="outline" className={meta.className}>
      {meta.emoji} {meta.label}
    </Badge>
  )
}

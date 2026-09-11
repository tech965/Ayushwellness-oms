import { CheckCircle2, CircleHelp, OctagonAlert, TriangleAlert } from "lucide-react"

import { cn } from "@/lib/utils"
import type { AddressValidationStatus } from "@/types/order"

interface AddressValidationBadgeProps {
  status: AddressValidationStatus | null | undefined
  score: number | null | undefined
  /** `false` when the order has no shipping address at all -- shown as
   * "No address on file" rather than "Validation pending" (which would
   * otherwise wrongly imply a result is still on its way). Defaults to
   * `true`: most callers (list/queue rows) don't have the full address
   * on hand to know the difference, and "pending" is the correct read
   * for the overwhelming majority case there.
   */
  hasAddress?: boolean
  className?: string
}

const STATUS_CONFIG: Record<
  AddressValidationStatus,
  { label: string; icon: typeof CheckCircle2; dotClass: string; textClass: string }
> = {
  valid: {
    label: "Valid Address",
    icon: CheckCircle2,
    dotClass: "bg-lime-500",
    textClass: "text-lime-700 dark:text-lime-400",
  },
  ambiguous: {
    label: "Ambiguous Address",
    icon: TriangleAlert,
    dotClass: "bg-amber-500",
    textClass: "text-amber-700 dark:text-amber-400",
  },
  junk: {
    label: "Junk Address",
    icon: OctagonAlert,
    dotClass: "bg-red-500",
    textClass: "text-red-700 dark:text-red-400",
  },
  unknown: {
    label: "Validation Unavailable",
    icon: CircleHelp,
    dotClass: "bg-muted-foreground/50",
    textClass: "text-muted-foreground",
  },
}

/** Compact Shiprocket-style address-confidence indicator -- a colored
 * dot + prominent-but-small percentage + status label (🟢 95% — Valid
 * Address / 🟡 50% — Ambiguous Address / 🔴 24% — Junk Address), styled
 * with this OMS's own existing tone system rather than copying
 * Shiprocket's UI. `status`/`score` are `null`/`undefined` for "never
 * validated yet" -- rendered as "Validation pending", never guessed as
 * any real status.
 */
export function AddressValidationBadge({
  status,
  score,
  hasAddress = true,
  className,
}: AddressValidationBadgeProps) {
  if (!status) {
    return (
      <span
        className={cn(
          "text-muted-foreground inline-flex items-center gap-1.5 text-xs",
          className
        )}
      >
        <span className="bg-muted-foreground/30 size-2 rounded-full" />
        {hasAddress ? "Validation pending" : "No address on file"}
      </span>
    )
  }

  const config = STATUS_CONFIG[status]
  const Icon = config.icon
  const showScore = status !== "unknown" && typeof score === "number"

  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 text-xs font-medium",
        config.textClass,
        className
      )}
    >
      <Icon className="size-3.5 shrink-0" />
      {showScore && <span className="font-semibold tabular-nums">{score}%</span>}
      <span className="whitespace-nowrap">{config.label}</span>
    </span>
  )
}

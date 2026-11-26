import { formatMoney } from "@/lib/format"
import { formatPercent } from "@/lib/percent"
import type { PercentageItem, RemittanceStatus } from "@/types/remittance"

/** Money from a decimal string, rupees with two decimals. */
export function rupees(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === "") return "—"
  return formatMoney(value)
}

/** A signed difference: "+₹200.00" / "-₹400.00" / "₹0.00". */
export function signedRupees(value: string | number): string {
  const n = Number(value)
  if (!Number.isFinite(n)) return "—"
  if (n === 0) return formatMoney(0)
  return `${n > 0 ? "+" : "-"}${formatMoney(Math.abs(n))}`
}

/** "17 of 100 delivered COD orders" / "₹1,000.00 of ₹6,000.00 expected COD collection". */
export function percentageBasis(item: PercentageItem): string {
  const fmt = (v: string) =>
    item.kind === "value" ? rupees(v) : Number(v).toLocaleString("en-IN")
  return `${fmt(item.numerator)} ${item.numerator_label} of ${fmt(item.denominator)} ${item.denominator_label}`
}

export function percentageText(item: PercentageItem): string {
  return formatPercent(item.value)
}

export const STATUS_STYLE: Record<
  RemittanceStatus,
  { label: string; color: string; className: string }
> = {
  pending: {
    label: "Pending",
    color: "#f59e0b",
    className: "bg-amber-100 text-amber-800 dark:bg-amber-500/15 dark:text-amber-400",
  },
  partially_reconciled: {
    label: "Partially Reconciled",
    color: "#38bdf8",
    className: "bg-sky-100 text-sky-800 dark:bg-sky-500/15 dark:text-sky-400",
  },
  matched: {
    label: "Matched",
    color: "#65a30d",
    className: "bg-lime-100 text-lime-800 dark:bg-lime-500/15 dark:text-lime-300",
  },
  short: {
    label: "Short",
    color: "#ef4444",
    className: "bg-red-100 text-red-800 dark:bg-red-500/15 dark:text-red-400",
  },
  excess: {
    label: "Excess",
    color: "#a855f7",
    className: "bg-purple-100 text-purple-800 dark:bg-purple-500/15 dark:text-purple-400",
  },
  failed: {
    label: "Failed",
    color: "#6b7280",
    className: "bg-muted text-muted-foreground",
  },
  refunded: {
    label: "Refunded",
    color: "#f97316",
    className: "bg-orange-100 text-orange-800 dark:bg-orange-500/15 dark:text-orange-400",
  },
}

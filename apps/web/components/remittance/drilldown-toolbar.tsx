"use client"

import { CheckCircle2, Download, TriangleAlert } from "lucide-react"

import {
  DateRangePicker,
  type DateRangeValue,
} from "@/components/shared/date-range-picker"
import { Button } from "@/components/ui/button"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { rupees, STATUS_STYLE } from "@/lib/remittance-format"
import type { DrillTotals, KpiAmount, RemittanceStatus } from "@/types/remittance"

const ALL = "__all__"

const records = (n: number) =>
  `${n.toLocaleString("en-IN")} ${n === 1 ? "record" : "records"}`

/** Money is compared in whole paise so "100.00" and "100" are equal. */
const paise = (value: string | number) => Math.round(Number(value) * 100)

interface TotalsProps {
  totals: DrillTotals | undefined
  /** The KPI behind the clicked card, or undefined for chart / table selections. */
  card?: KpiAmount
  /** What the open figure means. */
  definition?: string
  searching: boolean
  /** Sum of the rows on the page currently shown. */
  pageAmount: number
}

/** The exact total of every record in the drilldown (all pages), and whether it
 * equals the card that was clicked.
 */
export function DrilldownTotals({
  totals,
  card,
  definition,
  searching,
  pageAmount,
}: TotalsProps) {
  if (!totals) return null
  const matches =
    card !== undefined &&
    paise(totals.base_amount) === paise(card.amount) &&
    totals.base_count === Number(card.count)
  return (
    <div
      className="bg-muted/40 space-y-1 rounded-lg border p-3"
      data-testid="drilldown-totals"
    >
      <p className="text-sm">
        <span className="text-muted-foreground">Total — {totals.measure}: </span>
        <span
          className="text-lg font-semibold tabular-nums"
          data-testid="drilldown-total"
        >
          {rupees(totals.amount)}
        </span>
        <span className="text-muted-foreground"> · {records(totals.count)}</span>
      </p>
      {definition && (
        <p className="text-xs" data-testid="drilldown-definition">
          {definition}
        </p>
      )}
      {totals.breakdown.length > 0 && (
        <p className="text-muted-foreground text-xs">
          {totals.breakdown
            .map((part) => `${part.label}: ${rupees(part.amount)} (${part.count})`)
            .join(" · ")}
        </p>
      )}
      {searching && (
        <p className="text-muted-foreground text-xs">
          Search results only. Without the search: {rupees(totals.base_amount)} ·{" "}
          {records(totals.base_count)}.
        </p>
      )}
      {card !== undefined &&
        (matches ? (
          <p className="flex items-center gap-1 text-xs text-emerald-700 dark:text-emerald-400">
            <CheckCircle2 className="size-3.5 shrink-0" />
            Matches the card: {rupees(card.amount)} · {records(Number(card.count))}
          </p>
        ) : (
          <p
            className="flex items-center gap-1 text-xs text-amber-700 dark:text-amber-400"
            role="alert"
          >
            <TriangleAlert className="size-3.5 shrink-0" />
            The card shows {rupees(card.amount)} ({records(Number(card.count))}) — the
            data changed since it loaded. Refresh the page.
          </p>
        ))}
      <p className="text-muted-foreground text-xs">
        All pages are included in the total. This page adds up to {rupees(pageAmount)}.
      </p>
    </div>
  )
}

interface FiltersProps {
  range: DateRangeValue
  onRangeChange: (range: DateRangeValue) => void
  status: string | undefined
  onStatusChange: (status: string | undefined) => void
  statuses: RemittanceStatus[]
  /** Unmatched evidence belongs to no order, so it has no reconciliation status. */
  showStatus: boolean
  onExport: () => void
  exporting: boolean
}

/** Date + status filters and export inside the drilldown. They are the SAME
 * filters as the cards above, so a card and its records never disagree.
 */
export function DrilldownFilters({
  range,
  onRangeChange,
  status,
  onStatusChange,
  statuses,
  showStatus,
  onExport,
  exporting,
}: FiltersProps) {
  return (
    <div className="space-y-1" data-testid="drilldown-filters">
      <div className="flex flex-wrap items-center gap-2">
        <DateRangePicker value={range} onChange={onRangeChange} />
        {showStatus && (
          <Select
            value={status ?? ALL}
            onValueChange={(value) => onStatusChange(value === ALL ? undefined : value)}
          >
            <SelectTrigger className="w-[190px]" aria-label="Drilldown status">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL}>All status</SelectItem>
              {statuses.map((s) => (
                <SelectItem key={s} value={s}>
                  {STATUS_STYLE[s].label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}
        <Button size="sm" variant="outline" onClick={onExport} disabled={exporting}>
          <Download className="size-4" />
          {exporting ? "Exporting…" : "Export these records"}
        </Button>
      </div>
      <p className="text-muted-foreground text-xs">
        {showStatus
          ? "Date and status are shared with the cards above, so the totals stay in step."
          : "Unmatched items have no reconciliation status; the date filter applies."}
      </p>
    </div>
  )
}

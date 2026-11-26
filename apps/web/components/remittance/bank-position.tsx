"use client"

import { TriangleAlert } from "lucide-react"

import { Button } from "@/components/ui/button"
import { rupees, signedRupees } from "@/lib/remittance-format"
import { definitionOf } from "@/lib/remittance-definitions"
import { cn } from "@/lib/utils"
import type { RemittanceModule, RemittanceSummary } from "@/types/remittance"

interface Props {
  module: RemittanceModule
  summary: RemittanceSummary
  /** Title of the open drilldown, to mark the pressed figure. */
  selectedTitle: string | undefined
  onSelect: (drill: string, title: string) => void
}

function Figure({
  title,
  value,
  note,
  tooltip,
  selected,
  onClick,
}: {
  title: string
  value: string
  note: string
  tooltip?: string
  selected: boolean
  onClick: () => void
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={tooltip}
      aria-pressed={selected}
      className={cn(
        "hover:border-primary/50 bg-card rounded-lg border p-3 text-left transition-colors",
        selected && "border-primary bg-primary/5"
      )}
    >
      <span className="text-muted-foreground block text-sm font-medium">{title}</span>
      <span className="block text-xl font-semibold tabular-nums">{value}</span>
      <span className="text-muted-foreground block text-xs">{note}</span>
    </button>
  )
}

/** Bank confirmation (including partial credits), the settlement difference with
 * the effect of manual adjustments, and unverified settlement lines. Every figure
 * opens its exact records. These are not KPI cards.
 */
export function BankPositionPanel({ module, summary, selectedTitle, onSelect }: Props) {
  const isCod = module === "cod"
  const kpis = summary.kpis
  const position = kpis.bank_position
  if (!position) return null
  const orders = (n: number | string | undefined) =>
    `${Number(n ?? 0)} ${Number(n ?? 0) === 1 ? "order" : "orders"}`
  const money = (value: number | string | undefined) => rupees(String(value ?? 0))
  const figure = (title: string, drill: string, value: string, note: string) => (
    <Figure
      title={title}
      value={value}
      note={note}
      tooltip={definitionOf(module, drill)}
      selected={selectedTitle === title}
      onClick={() => onSelect(drill, title)}
    />
  )
  const partial = kpis.partially_confirmed
  const adjusted = kpis.adjusted
  const difference = kpis.settlement_difference
  const unverified = kpis.unverified
  const over = Number(position.over_confirmed)
  const unverifiedLines = Number(unverified?.unmatched_lines ?? 0)

  return (
    <div className="space-y-4">
      <section
        className="space-y-2"
        aria-label="Bank confirmation"
        data-testid="bank-position"
      >
        <div>
          <h3 className="text-base font-semibold">Bank confirmation</h3>
          <p className="text-muted-foreground text-xs">
            Statement-matched {money(position.statement_matched)} · Manually verified{" "}
            {money(position.manually_verified)}
            {over > 0 &&
              ` · ${money(over)} confirmed above what some orders expected (not netted against the remaining amount)`}
          </p>
        </div>
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 xl:grid-cols-4">
          {figure(
            "Total expected",
            isCod ? "expected" : "expected_net",
            money(position.expected),
            isCod ? "COD amount of delivered orders" : "Expected net of paid orders"
          )}
          {figure(
            "Amount bank confirmed",
            isCod ? "bank_confirmed" : "bank_settlement",
            money(position.amount),
            "Actual credited amounts only"
          )}
          {figure(
            "Remaining amount",
            "bank_remaining",
            money(position.remaining),
            orders(kpis.bank_remaining?.count)
          )}
          {figure(
            "Partially Confirmed",
            "partially_confirmed",
            orders(partial?.count),
            `${money(partial?.confirmed)} confirmed of ${money(partial?.expected)} · ${money(partial?.amount)} remaining`
          )}
        </div>
      </section>

      <section
        className="space-y-2"
        aria-label="Difference and manual adjustments"
        data-testid="adjustment-position"
      >
        <h3 className="text-base font-semibold">
          {isCod ? "Manual adjustments" : "Settlement difference and manual adjustments"}
        </h3>
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
          {!isCod &&
            figure(
              "Settlement Difference (headline)",
              "difference",
              money(difference?.amount),
              "Expected Net − Total Settlement Received"
            )}
          {figure(
            "Manual adjustments",
            "adjusted",
            signedRupees(String(adjusted?.amount ?? 0)),
            orders(adjusted?.count)
          )}
          {!isCod && (
            <div className="bg-muted/40 rounded-lg border p-3">
              <span className="text-muted-foreground block text-sm font-medium">
                Difference less adjustments
              </span>
              <span className="block text-xl font-semibold tabular-nums">
                {money(difference?.after_adjustments)}
              </span>
              <span className="text-muted-foreground block text-xs">
                For information. The headline never includes adjustments.
              </span>
            </div>
          )}
        </div>
        <p className="text-muted-foreground text-xs">
          Per order, status difference = {isCod ? "COD settled" : "settlement received"} +
          adjustments − {isCod ? "COD amount" : "expected net"}. Open an order to see how
          its adjustment changes the status. Adjustments are never bank money.
        </p>
      </section>

      {!isCod && (Number(unverified?.count ?? 0) > 0 || unverifiedLines > 0) && (
        <div
          className="flex flex-wrap items-center gap-3 rounded-lg border border-amber-500/50 bg-amber-500/10 p-3 text-sm"
          data-testid="unverified-notice"
        >
          <TriangleAlert className="size-4 shrink-0 text-amber-700 dark:text-amber-400" />
          <span className="min-w-0 flex-1">
            Unverified settlement status, counted in no total: {money(unverified?.amount)}{" "}
            on {orders(unverified?.count)}
            {unverifiedLines > 0 &&
              `, and ${unverifiedLines} unmatched line(s) worth ${money(unverified?.unmatched_amount)} (listed under Unmatched Transactions)`}
            .
          </span>
          {Number(unverified?.count ?? 0) > 0 && (
            <Button
              size="sm"
              variant="outline"
              title={definitionOf(module, "unverified")}
              aria-pressed={selectedTitle === "Unverified lines"}
              onClick={() => onSelect("unverified", "Unverified lines")}
            >
              Unverified lines
            </Button>
          )}
        </div>
      )}
    </div>
  )
}

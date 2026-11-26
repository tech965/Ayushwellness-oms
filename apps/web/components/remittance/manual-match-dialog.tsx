"use client"

import * as React from "react"
import { toast } from "sonner"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { getApiErrorMessage } from "@/lib/api-client"
import { formatDate } from "@/lib/format"
import { rupees, signedRupees, STATUS_STYLE } from "@/lib/remittance-format"
import { cn } from "@/lib/utils"
import { useConfirmedMatch, useLookupOrderForLine } from "@/services/remittance"
import type { MatchLookup, UnmatchedRow } from "@/types/remittance"

interface Props {
  line: UnmatchedRow
  open: boolean
  onOpenChange: (open: boolean) => void
}

function Line({
  label,
  value,
  strong,
}: {
  label: string
  value: React.ReactNode
  strong?: boolean
}) {
  return (
    <div className={cn("flex justify-between gap-4 text-sm", strong && "font-semibold")}>
      <span className="text-muted-foreground">{label}</span>
      <span className="tabular-nums">{value}</span>
    </div>
  )
}

/** Manually match one unmatched COD remittance line to an order.
 *
 * 1. Search by the EXACT OMS order id / order number or AWB (no fuzzy search).
 * 2. The server previews the result: expected COD, this remittance, other
 *    remittances already on the order, the difference and the resulting
 *    status -- including partial settlements and excess.
 * 3. A blocked order (not COD, not delivered, already matched) cannot be
 *    confirmed; a match that leaves a difference needs the user to accept it;
 *    saving always needs an explicit confirmation. The action is audit-logged.
 */
export function ManualMatchDialog({ line, open, onOpenChange }: Props) {
  const [query, setQuery] = React.useState("")
  const [lookup, setLookup] = React.useState<MatchLookup | null>(null)
  const [confirmed, setConfirmed] = React.useState(false)
  const [acknowledged, setAcknowledged] = React.useState(false)
  const lookupMutation = useLookupOrderForLine()
  const matchMutation = useConfirmedMatch()
  const preview = lookup?.preview ?? null

  function resetResult() {
    setLookup(null)
    setConfirmed(false)
    setAcknowledged(false)
  }

  async function find() {
    resetResult()
    try {
      setLookup(await lookupMutation.mutateAsync({ line_id: line.id, q: query.trim() }))
    } catch (error) {
      toast.error(getApiErrorMessage(error))
    }
  }

  async function save() {
    if (!preview) return
    try {
      await matchMutation.mutateAsync({
        line_id: line.id,
        order_id: preview.order_id,
        acknowledge_difference: acknowledged,
      })
      toast.success(`Remittance matched to ${preview.order_number}.`)
      onOpenChange(false)
    } catch (error) {
      toast.error(getApiErrorMessage(error))
    }
  }

  const blocked = Boolean(preview && preview.blockers.length > 0)
  const needsAck = Boolean(preview?.needs_acknowledgement)
  const canSave = Boolean(preview && !blocked && confirmed && (!needsAck || acknowledged))

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Match remittance to an order</DialogTitle>
          <DialogDescription>
            Find the delivered COD order this remittance belongs to by its exact order ID
            or AWB. You will see the amounts and the effect before anything is saved.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-1.5 rounded-lg border p-3" data-testid="match-line">
          <Line label="Shipping partner" value={line.partner ?? "—"} />
          <Line label="AWB" value={line.awb ?? "—"} />
          <Line label="Order reference in report" value={line.order_ref ?? "—"} />
          <Line label="Remittance reference" value={line.reference ?? "—"} />
          <Line label="Remittance date" value={line.date ? formatDate(line.date) : "—"} />
          <Line label="Remittance amount" value={rupees(line.amount)} strong />
        </div>

        <form
          className="flex items-end gap-2"
          onSubmit={(event) => {
            event.preventDefault()
            if (query.trim()) void find()
          }}
        >
          <div className="min-w-0 flex-1 space-y-1.5">
            <Label htmlFor="match-query">OMS order ID or AWB</Label>
            <Input
              id="match-query"
              value={query}
              onChange={(event) => {
                setQuery(event.target.value)
                resetResult()
              }}
              placeholder="Exact order number, order ID or AWB"
              autoComplete="off"
            />
          </div>
          <Button
            type="submit"
            variant="secondary"
            disabled={!query.trim() || lookupMutation.isPending}
          >
            {lookupMutation.isPending ? "Searching…" : "Find order"}
          </Button>
        </form>

        {lookup && !lookup.found && (
          <p className="text-sm text-red-700 dark:text-red-400" role="alert">
            No order has exactly that order ID or AWB. Partial matches are not offered.
          </p>
        )}

        {preview && (
          <div className="space-y-3" data-testid="match-preview">
            <div className="space-y-1.5 rounded-lg border p-3">
              <div className="flex items-center justify-between gap-2">
                <span className="font-semibold">{preview.order_number}</span>
                <Badge
                  variant="outline"
                  className={cn(
                    "rounded-md border-transparent font-semibold",
                    STATUS_STYLE[preview.projected_status].className
                  )}
                >
                  After match: {preview.projected_status_label}
                </Badge>
              </div>
              <p className="text-muted-foreground text-xs">
                {preview.customer_name ?? "No customer"} · AWB {preview.awb ?? "—"}
              </p>
              <Line label="Expected COD amount" value={rupees(preview.expected_amount)} />
              <Line label="This remittance" value={rupees(preview.line_amount)} />
              <Line
                label="Already remitted on this order"
                value={rupees(preview.other_remitted)}
              />
              {Number(preview.adjustment_amount) !== 0 && (
                <Line
                  label="Manual adjustments"
                  value={rupees(preview.adjustment_amount)}
                />
              )}
              <Line label="Total after match" value={rupees(preview.projected_total)} />
              <Line
                label="Difference from expected"
                value={signedRupees(preview.projected_difference)}
                strong
              />
            </div>
            {preview.blockers.map((b) => (
              <p
                key={b}
                className="text-sm font-medium text-red-700 dark:text-red-400"
                role="alert"
              >
                {b}
              </p>
            ))}
            {preview.warnings.map((w) => (
              <p key={w} className="text-sm text-amber-700 dark:text-amber-400">
                {w}
              </p>
            ))}
            {!blocked && (
              <div className="space-y-2">
                {needsAck && (
                  <label className="flex items-start gap-2 text-sm">
                    <input
                      type="checkbox"
                      className="mt-1"
                      checked={acknowledged}
                      onChange={(event) => setAcknowledged(event.target.checked)}
                    />
                    <span>
                      I accept the difference of{" "}
                      {signedRupees(preview.projected_difference)} (a partial settlement
                      or excess) for this order.
                    </span>
                  </label>
                )}
                <label className="flex items-start gap-2 text-sm">
                  <input
                    type="checkbox"
                    className="mt-1"
                    checked={confirmed}
                    onChange={(event) => setConfirmed(event.target.checked)}
                  />
                  <span>
                    I confirm this remittance of {rupees(preview.line_amount)} belongs to
                    order {preview.order_number}. This is recorded in the audit log.
                  </span>
                </label>
              </div>
            )}
          </div>
        )}

        <DialogFooter>
          <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            type="button"
            disabled={!canSave || matchMutation.isPending}
            onClick={() => void save()}
          >
            {matchMutation.isPending ? "Saving…" : "Match remittance"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

"use client"

import * as React from "react"
import { toast } from "sonner"

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
import { rupees, signedRupees } from "@/lib/remittance-format"
import { useAddAdjustment } from "@/services/remittance"
import type { CodRecord, PrepaidRecord, RemittanceModule } from "@/types/remittance"

interface Props {
  module: RemittanceModule
  record: CodRecord | PrepaidRecord
  open: boolean
  onOpenChange: (open: boolean) => void
}

/** A reasoned manual adjustment on one order (for example an agreed
 * write-off of a short remittance). It closes the difference but is never
 * counted as bank evidence, is audit-logged, and can be reversed.
 */
export function AdjustDialog({ module, record, open, onOpenChange }: Props) {
  const suggested = String(-Number(record.difference))
  const [amount, setAmount] = React.useState(
    Number(record.difference) === 0 ? "" : suggested
  )
  const [reason, setReason] = React.useState("")
  const mutation = useAddAdjustment(module)

  async function submit() {
    try {
      await mutation.mutateAsync({ order_id: record.order_id, amount, reason })
      toast.success("Adjustment recorded.")
      onOpenChange(false)
    } catch (error) {
      toast.error(getApiErrorMessage(error))
    }
  }

  const expected =
    module === "cod"
      ? (record as CodRecord).expected_amount
      : (record as PrepaidRecord).expected_net

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Adjust {record.order_number}</DialogTitle>
          <DialogDescription>
            Expected {rupees(expected)} · current difference{" "}
            {signedRupees(record.difference)}. A positive amount counts toward what is
            settled. This does not count as bank evidence and is recorded in the audit
            trail.
          </DialogDescription>
        </DialogHeader>
        <form
          className="space-y-4"
          onSubmit={(event) => {
            event.preventDefault()
            void submit()
          }}
        >
          <div className="space-y-1.5">
            <Label htmlFor="adjust-amount">Adjustment amount (₹)</Label>
            <Input
              id="adjust-amount"
              inputMode="decimal"
              value={amount}
              onChange={(event) => setAmount(event.target.value)}
              required
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="adjust-reason">Reason (required)</Label>
            <Input
              id="adjust-reason"
              value={reason}
              onChange={(event) => setReason(event.target.value)}
              minLength={3}
              maxLength={255}
              required
            />
          </div>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button
              type="submit"
              disabled={mutation.isPending || !amount || reason.trim().length < 3}
            >
              {mutation.isPending ? "Saving…" : "Record adjustment"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

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
import {
  useEditMarketplaceMovement,
  useUndoMarketplaceMovement,
} from "@/services/platform-inventory"
import type { ProductMarketplaceMovement } from "@/types/platform-inventory"

export const MOVEMENT_TYPE_LABELS: Record<string, string> = {
  stock_added: "Stock Added",
  sale: "Sale",
  rto: "RTO",
  reversal: "Reversal",
}

function packets(n: number): string {
  return `${n.toLocaleString()} ${n === 1 ? "packet" : "packets"}`
}

interface MovementDialogProps {
  movement: ProductMarketplaceMovement
  onClose: () => void
}

/** "Edit Marketplace Movement" -- corrects a manual Sale/RTO's packet
 * quantity. There is NO SKU field and the platform / movement type are
 * shown read-only: only the quantity (and a required reason) change. The
 * original row is never altered; the backend appends a reversal and a
 * replacement (see `PlatformInventoryService.edit_movement`).
 */
export function EditMarketplaceMovementDialog({ movement, onClose }: MovementDialogProps) {
  const edit = useEditMarketplaceMovement(movement.id)
  const [quantity, setQuantity] = React.useState("")
  const [reason, setReason] = React.useState("")

  const parsed = Number(quantity)
  const validQuantity =
    quantity !== "" &&
    Number.isInteger(parsed) &&
    parsed > 0 &&
    parsed !== movement.quantity_packets
  const canSave = validQuantity && reason.trim().length > 0 && !edit.isPending

  async function save() {
    try {
      await edit.mutateAsync({ quantity_packets: parsed, reason: reason.trim() })
      toast.success(
        `Edited from ${movement.quantity_packets} → ${parsed} packets. History is preserved.`
      )
      onClose()
    } catch (error) {
      toast.error(getApiErrorMessage(error))
    }
  }

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>Edit Marketplace Movement</DialogTitle>
          <DialogDescription>
            The original record is kept; a reversal and a corrected entry are added.
          </DialogDescription>
        </DialogHeader>
        <div className="flex flex-col gap-3 text-sm">
          <div>
            Platform: <span className="font-semibold">{movement.platform_label}</span>
          </div>
          <div>
            Movement Type:{" "}
            <span className="font-semibold">
              {MOVEMENT_TYPE_LABELS[movement.movement_type] ?? movement.movement_type}
            </span>
          </div>
          <div>
            Current Quantity:{" "}
            <span className="font-semibold">{packets(movement.quantity_packets)}</span>
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="edit-marketplace-quantity">New Quantity</Label>
            <Input
              id="edit-marketplace-quantity"
              type="number"
              min={1}
              className="w-32"
              value={quantity}
              onChange={(e) => setQuantity(e.target.value)}
              placeholder="0"
              autoFocus
            />
            <p className="text-muted-foreground text-xs">packets</p>
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="edit-marketplace-reason">Reason</Label>
            <Input
              id="edit-marketplace-reason"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="e.g. correction"
            />
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button disabled={!canSave} onClick={save}>
            {edit.isPending ? "Saving..." : "Save"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

/** "Undo" -- appends a reversal; the original record stays in the history
 * and the effective balance returns to its pre-movement value. A reason
 * is required for the audit trail.
 */
export function UndoMarketplaceMovementDialog({ movement, onClose }: MovementDialogProps) {
  const undo = useUndoMarketplaceMovement(movement.id)
  const [reason, setReason] = React.useState("")
  const canConfirm = reason.trim().length > 0 && !undo.isPending
  const kind = MOVEMENT_TYPE_LABELS[movement.movement_type] ?? movement.movement_type

  async function confirm() {
    try {
      await undo.mutateAsync({ reason: reason.trim() })
      toast.success(`${kind} undone. A reversal was recorded; the original is kept in history.`)
      onClose()
    } catch (error) {
      toast.error(getApiErrorMessage(error))
    }
  }

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>Undo Marketplace Movement</DialogTitle>
          <DialogDescription>
            {movement.platform_label} {kind} of {packets(movement.quantity_packets)} will be
            reversed. The original is not deleted -- a reversal entry is added to the history.
          </DialogDescription>
        </DialogHeader>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="undo-marketplace-reason">Reason</Label>
          <Input
            id="undo-marketplace-reason"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="e.g. entered by mistake"
            autoFocus
          />
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button disabled={!canConfirm} onClick={confirm}>
            {undo.isPending ? "Saving..." : "Undo"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

"use client"

import * as React from "react"
import { ExternalLink } from "lucide-react"

import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { SHIPROCKET_READY_TO_SHIP_URL } from "@/lib/shiprocket"
import type { ProcessExistingShipmentResult } from "@/types/shipment"

export interface ProcessShipmentDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  isPending: boolean
  result: ProcessExistingShipmentResult | null
}

/** Shown by "Process Shipment"/"Ship Order" -- the API equivalent of
 * Shiprocket's own dashboard "Bulk Ship Orders" action for one order
 * (`useProcessExistingShipments`/`useProcessExistingShipmentsForMyScope`).
 * NEVER creates a Shiprocket order: the mutation this dialog reports on
 * only resolves an order's EXISTING Shiprocket shipment (unchanged
 * `locate_shiprocket_orders` matching) and assigns an AWB to it, skipping
 * any shipment that already has one.
 *
 * Displays the real outcome -- courier + AWB on success, the existing AWB
 * when already processed, or the exact failure reason -- and offers a
 * button to open Shiprocket's plain "Ready to Ship" page (never templated
 * with `?order_ids=`; Shiprocket has no supported deep-link for a
 * specific order). Opening that page is entirely separate from
 * processing: nothing here reads or writes the clipboard as part of this
 * flow, since the outcome is shown directly in this dialog, not handed to
 * the operator to paste somewhere.
 */
export function ProcessShipmentDialog({
  open,
  onOpenChange,
  isPending,
  result,
}: ProcessShipmentDialogProps) {
  function openReadyToShip() {
    window.open(SHIPROCKET_READY_TO_SHIP_URL, "_blank", "noopener,noreferrer")
  }

  const orderLabel = result?.order_number ?? "This order"

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-sm">
        <DialogHeader>
          <DialogTitle>Process Shipment</DialogTitle>
          <DialogDescription asChild>
            <div className="flex flex-col gap-2 text-left">
              {isPending || !result ? (
                <span>Processing {orderLabel} via Shiprocket…</span>
              ) : result.status === "success" ? (
                <span>
                  {orderLabel} processed — Courier: {result.courier_name ?? "unknown"} — AWB:{" "}
                  {result.awb}
                </span>
              ) : result.status === "skipped" ? (
                <span>
                  {orderLabel} already has AWB {result.awb}
                  {result.courier_name ? ` (${result.courier_name})` : ""}.
                </span>
              ) : (
                <span>
                  {orderLabel} failed — {result.reason ?? "Unknown error."}
                </span>
              )}
            </div>
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button onClick={openReadyToShip} disabled={isPending}>
            <ExternalLink className="size-3.5" />
            Open Shiprocket Ready to Ship
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

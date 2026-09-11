"use client"

import * as React from "react"
import { AlertTriangle, CheckCircle2, ExternalLink, XCircle } from "lucide-react"

import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { Button } from "@/components/ui/button"
import { SHIPROCKET_READY_TO_SHIP_URL } from "@/lib/shiprocket"
import { useProcessExistingShipments } from "@/services/orders"
import type { ProcessExistingShipmentsResponse } from "@/types/shipment"

export interface BulkShipRow {
  id: string
  order_number: string
}

interface BulkShipDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  rows: BulkShipRow[]
  /** Called once the dialog is closed so the caller can clear its
   * selection.
   */
  onDone: () => void
}

/** Shared bulk "Process Shipment" flow for both "Orders Need Shipment"
 * and "Confirmed by Telecaller" -- reused rather than reimplemented per
 * page, so the two can never drift on behavior.
 *
 * The API equivalent of Shiprocket's own dashboard "Bulk Ship Orders"
 * action: on open, calls `useProcessExistingShipments` (`POST
 * /orders/bulk-process-shipments`) for every selected order, which
 * resolves each one's EXISTING Shiprocket shipment (via the unchanged
 * `locate_shiprocket_orders`) and assigns it an AWB -- skipping any that
 * already have one. NEVER calls a Shiprocket create-shipment API (no
 * `orders/create/adhoc` anywhere in this path) -- the same rule
 * `ShipmentActionCell`'s single-row actions follow, and for the identical
 * reason: Shiprocket may already have an order for one of these (e.g. via
 * its own Shopify channel connector), so creating one here risked a real
 * duplicate shipment.
 *
 * Every order gets its own result -- processed, already-processed
 * (skipped), or failed with the real reason -- never all-or-nothing.
 * "Open Shiprocket Ready to Ship" is offered afterward as a plain,
 * unfiltered link (Shiprocket has no supported deep-link for specific
 * orders), for whatever manual follow-up an operator wants.
 */
export function BulkShipDialog({ open, onOpenChange, rows, onDone }: BulkShipDialogProps) {
  const processShipment = useProcessExistingShipments()
  const orderIds = React.useMemo(() => rows.map((r) => r.id), [rows])
  const rowsByOrderId = React.useMemo(() => new Map(rows.map((r) => [r.id, r])), [rows])
  const [data, setData] = React.useState<ProcessExistingShipmentsResponse | null>(null)

  React.useEffect(() => {
    if (open && orderIds.length > 0) {
      processShipment.mutate(orderIds, {
        onSuccess: setData,
        onError: () =>
          setData({
            processed_count: 0,
            skipped_count: 0,
            failed_count: orderIds.length,
            results: orderIds.map((id) => ({
              order_id: id,
              order_number: rowsByOrderId.get(id)?.order_number ?? null,
              status: "failed",
              shiprocket_shipment_id: null,
              shiprocket_order_id: null,
              awb: null,
              courier_name: null,
              reason: "Could not reach Shiprocket for this order.",
            })),
          }),
      })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open])

  const results = data?.results ?? []
  const isLoading = processShipment.isPending

  function openReadyToShip() {
    window.open(SHIPROCKET_READY_TO_SHIP_URL, "_blank", "noopener,noreferrer")
  }

  function handleClose(nextOpen: boolean) {
    onOpenChange(nextOpen)
    if (!nextOpen) {
      setData(null)
      onDone()
    }
  }

  return (
    <AlertDialog open={open} onOpenChange={handleClose}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>
            {data
              ? `Processed ${data.processed_count} of ${rows.length} selected orders`
              : `Process ${rows.length} selected orders`}
          </AlertDialogTitle>
          <AlertDialogDescription asChild>
            <div className="flex flex-col gap-3 text-left">
              {isLoading || !data ? (
                <span>Processing each selected order via Shiprocket…</span>
              ) : (
                <>
                  <div className="flex flex-wrap gap-3 text-xs">
                    <span className="flex items-center gap-1 text-green-700 dark:text-green-400">
                      <CheckCircle2 className="size-3.5" />
                      Processed: {data.processed_count}
                    </span>
                    <span className="text-muted-foreground flex items-center gap-1">
                      <AlertTriangle className="size-3.5" />
                      Already processed: {data.skipped_count}
                    </span>
                    <span className="text-destructive flex items-center gap-1">
                      <XCircle className="size-3.5" />
                      Failed: {data.failed_count}
                    </span>
                  </div>
                  <ul className="border-border flex max-h-64 flex-col gap-1 overflow-y-auto rounded-md border p-2 text-xs">
                    {results.map((r) => (
                      <li key={r.order_id} className="flex items-start justify-between gap-2">
                        <span className="font-medium">
                          {rowsByOrderId.get(r.order_id)?.order_number ??
                            r.order_number ??
                            r.order_id}
                        </span>
                        <span className="text-right">
                          {r.status === "success" && (
                            <span className="text-green-700 dark:text-green-400">
                              Courier: {r.courier_name ?? "unknown"} — AWB: {r.awb}
                            </span>
                          )}
                          {r.status === "skipped" && (
                            <span className="text-muted-foreground">
                              Already has AWB {r.awb}
                            </span>
                          )}
                          {r.status === "failed" && (
                            <span className="text-destructive">{r.reason ?? "Failed"}</span>
                          )}
                        </span>
                      </li>
                    ))}
                  </ul>
                </>
              )}
            </div>
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>Close</AlertDialogCancel>
          <Button onClick={openReadyToShip} disabled={isLoading}>
            <ExternalLink className="size-3.5" />
            Open Shiprocket Ready to Ship
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}

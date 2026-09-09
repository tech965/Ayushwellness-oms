"use client"

import * as React from "react"
import { toast } from "sonner"

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
import { getApiErrorMessage } from "@/lib/api-client"
import { formatMoney } from "@/lib/format"
import { useBulkShipOrders, useValidateBulkShip } from "@/services/shipment-queue"

export interface BulkShipRow {
  id: string
  order_number: string
  payment_type: string
  total_amount: string
}

interface BulkShipDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  rows: BulkShipRow[]
  /** Called once processing finishes (whether or not the dialog is
   * closed programmatically) so the caller can clear its selection —
   * spec: "After successful processing: clear successful rows from
   * selection." Simplified to clearing the whole selection once any
   * shipments were created, matching `useBulkShipOrders`' own existing
   * queue-page behavior.
   */
  onDone: () => void
}

/** Shared bulk-ship confirmation flow for both "Orders Need Shipment" and
 * "Confirmed by Telecaller" — reused rather than reimplemented per page,
 * so the two can never drift on validation or execution behavior.
 *
 * Two-step, matching spec exactly:
 *   1. On open, dry-run validates every selected order (`POST /orders/
 *      bulk-ship/validate` — reuses `ShiprocketOperationsService.
 *      _check_shippable`, the SAME rule `create_shipment_for_order`
 *      itself enforces; never a second, looser copy of it) and shows
 *      ready/not-ready counts with per-order reasons. Nothing is created
 *      yet at this point.
 *   2. Only the READY subset is actually shipped (`POST /orders/
 *      bulk-ship`, the existing bulk endpoint) — never blindly every
 *      selected row. One order's failure during execution never blocks
 *      or rolls back the others (existing endpoint behavior); the result
 *      toast reports the final shipped/failed counts.
 */
export function BulkShipDialog({ open, onOpenChange, rows, onDone }: BulkShipDialogProps) {
  const validate = useValidateBulkShip()
  const bulkShip = useBulkShipOrders()
  const orderIds = React.useMemo(() => rows.map((r) => r.id), [rows])

  React.useEffect(() => {
    if (open && orderIds.length > 0) {
      validate.mutate(orderIds)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open])

  const results = validate.data
  const readyIds = React.useMemo(
    () => new Set((results ?? []).filter((r) => r.ready).map((r) => r.order_id)),
    [results]
  )
  const notReady = (results ?? []).filter((r) => !r.ready)
  const readyRows = rows.filter((r) => readyIds.has(r.id))
  const rowsByOrderId = new Map(rows.map((r) => [r.id, r]))

  const codCount = readyRows.filter((r) => r.payment_type === "cod").length
  const prepaidCount = readyRows.filter((r) => r.payment_type === "prepaid").length
  const totalValue = readyRows.reduce((sum, r) => sum + Number(r.total_amount || 0), 0)

  function handleClose(nextOpen: boolean) {
    if (!bulkShip.isPending) onOpenChange(nextOpen)
  }

  function handleShip() {
    bulkShip.mutate(Array.from(readyIds), {
      onSuccess: (result) => {
        if (result.failed_count === 0) {
          toast.success(`${result.shipped_count} shipment(s) created via Shiprocket.`)
        } else {
          toast.warning(
            `${result.shipped_count} shipped, ${result.failed_count} could not be shipped.`
          )
        }
        onOpenChange(false)
        onDone()
      },
      onError: (error) => toast.error(getApiErrorMessage(error)),
    })
  }

  const isValidating = validate.isPending
  const hasValidated = results !== undefined

  return (
    <AlertDialog open={open} onOpenChange={handleClose}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>
            {hasValidated && notReady.length > 0
              ? `${rows.length} selected — ${readyRows.length} ready, ${notReady.length} cannot be shipped`
              : `Create shipments for ${rows.length} selected orders?`}
          </AlertDialogTitle>
          <AlertDialogDescription asChild>
            <div className="flex flex-col gap-3 text-left">
              {isValidating ? (
                <span>Checking stock, address, and confirmation status for each order…</span>
              ) : (
                <>
                  <div className="grid grid-cols-3 gap-2 text-sm">
                    <div>
                      <div className="text-muted-foreground text-xs">COD</div>
                      <div className="font-medium">{codCount}</div>
                    </div>
                    <div>
                      <div className="text-muted-foreground text-xs">Prepaid</div>
                      <div className="font-medium">{prepaidCount}</div>
                    </div>
                    <div>
                      <div className="text-muted-foreground text-xs">Total value</div>
                      <div className="font-medium">{formatMoney(totalValue)}</div>
                    </div>
                  </div>
                  <span>
                    Creates a Shiprocket shipment for each ready order and syncs tracking to
                    Shopify once assigned. You still assign AWB/request pickup per shipment
                    afterward where needed.
                  </span>
                  {notReady.length > 0 && (
                    <div className="border-border rounded-md border p-2">
                      <p className="mb-1 text-xs font-semibold">
                        Not ready — excluded automatically:
                      </p>
                      <ul className="flex flex-col gap-0.5 text-xs">
                        {notReady.map((r) => (
                          <li key={r.order_id}>
                            <span className="font-medium">
                              {rowsByOrderId.get(r.order_id)?.order_number ?? r.order_id}
                            </span>
                            {": "}
                            {r.reason}
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}
                </>
              )}
            </div>
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={bulkShip.isPending}>Cancel</AlertDialogCancel>
          <Button
            onClick={handleShip}
            disabled={isValidating || readyRows.length === 0 || bulkShip.isPending}
          >
            {bulkShip.isPending
              ? "Shipping..."
              : notReady.length > 0
                ? `Ship ${readyRows.length} Ready Order${readyRows.length === 1 ? "" : "s"}`
                : "Create Shipments"}
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}

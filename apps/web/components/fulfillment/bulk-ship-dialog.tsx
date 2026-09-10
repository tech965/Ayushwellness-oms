"use client"

import * as React from "react"
import { ExternalLink } from "lucide-react"

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
import { useLocateShiprocketOrders } from "@/services/shipment-queue"

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

/** Shared bulk "open in Shiprocket" flow for both "Orders Need Shipment"
 * and "Confirmed by Telecaller" -- reused rather than reimplemented per
 * page, so the two can never drift on behavior.
 *
 * NEVER calls a Shiprocket create-shipment API (no `orders/create/adhoc`
 * anywhere in this path) -- the same rule `ShipmentActionCell`'s
 * single-row "Process Shipment"/"Ship Order" actions follow, and for the
 * identical reason: Shiprocket may already have an order for one of
 * these (e.g. via its own Shopify channel connector), so creating one
 * here risked a real duplicate shipment.
 *
 * On open, resolves every selected order's EXISTING Shiprocket order via
 * `POST /shipments/locate-shiprocket-order` (see
 * `app.services.shiprocket_service.locate_shiprocket_orders` -- exact
 * identifier matching only, never a guess). Each "Open" click is its own
 * direct user gesture -- opening every found tab automatically the
 * moment the (asynchronous) locate response arrives would be blocked by
 * most browsers' popup blockers, so tabs only ever open from a real
 * click here (either one row's "Open" button, or "Open All Found",
 * which fires its `window.open` calls synchronously from that same
 * click, after the results are already on screen).
 */
export function BulkShipDialog({ open, onOpenChange, rows, onDone }: BulkShipDialogProps) {
  const locate = useLocateShiprocketOrders()
  const orderIds = React.useMemo(() => rows.map((r) => r.id), [rows])
  const rowsByOrderId = React.useMemo(() => new Map(rows.map((r) => [r.id, r])), [rows])

  React.useEffect(() => {
    if (open && orderIds.length > 0) {
      locate.mutate(orderIds)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open])

  const results = locate.data ?? []
  const found = results.filter((r) => r.status === "found")
  const notFound = results.filter((r) => r.status !== "found")
  const hasResults = locate.data !== undefined
  const isLoading = locate.isPending

  function openOne(url: string) {
    window.open(url, "_blank", "noopener,noreferrer")
  }

  function openAllFound() {
    for (const r of found) {
      if (r.shiprocket_order_url) openOne(r.shiprocket_order_url)
    }
  }

  function handleClose(nextOpen: boolean) {
    onOpenChange(nextOpen)
    if (!nextOpen) onDone()
  }

  return (
    <AlertDialog open={open} onOpenChange={handleClose}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>
            {hasResults
              ? `${found.length} of ${rows.length} selected orders found in Shiprocket`
              : `Locate ${rows.length} selected orders in Shiprocket`}
          </AlertDialogTitle>
          <AlertDialogDescription asChild>
            <div className="flex flex-col gap-3 text-left">
              {isLoading ? (
                <span>Checking Shiprocket for each selected order…</span>
              ) : (
                <>
                  <span>
                    Opens each order&apos;s EXISTING Shiprocket order page in a new tab -- never
                    creates a new one. Ship it from within Shiprocket as usual.
                  </span>
                  {found.length > 0 && (
                    <div className="border-border rounded-md border p-2">
                      <p className="mb-1 text-xs font-semibold">Found — click to open:</p>
                      <ul className="flex flex-col gap-1 text-xs">
                        {found.map((r) => (
                          <li
                            key={r.order_id}
                            className="flex items-center justify-between gap-2"
                          >
                            <span className="font-medium">
                              {rowsByOrderId.get(r.order_id)?.order_number ?? r.order_id}
                            </span>
                            <Button
                              size="sm"
                              variant="outline"
                              className="h-6 px-2"
                              onClick={() =>
                                r.shiprocket_order_url && openOne(r.shiprocket_order_url)
                              }
                            >
                              <ExternalLink className="size-3" />
                              Open
                            </Button>
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}
                  {notFound.length > 0 && (
                    <div className="border-border rounded-md border p-2">
                      <p className="mb-1 text-xs font-semibold">
                        Could not be located — verify manually in Shiprocket:
                      </p>
                      <ul className="flex flex-col gap-0.5 text-xs">
                        {notFound.map((r) => (
                          <li key={r.order_id}>
                            <span className="font-medium">
                              {rowsByOrderId.get(r.order_id)?.order_number ?? r.order_id}
                            </span>
                            {r.message ? `: ${r.message}` : null}
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
          <AlertDialogCancel>Close</AlertDialogCancel>
          <Button onClick={openAllFound} disabled={isLoading || found.length === 0}>
            Open All Found ({found.length})
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}

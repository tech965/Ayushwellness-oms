"use client"

import * as React from "react"
import { Copy, ExternalLink } from "lucide-react"
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
import { copyToClipboard, SHIPROCKET_READY_TO_SHIP_URL } from "@/lib/shiprocket"
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
 * On open, resolves every selected order's EXISTING Shiprocket order id
 * via `POST /shipments/locate-shiprocket-order` (see
 * `app.services.shiprocket_service.locate_shiprocket_orders` -- exact
 * identifier matching only, never a guess). Shiprocket has no supported
 * deep-link URL for a specific order (confirmed by Shiprocket support),
 * so there's only ever ONE Shiprocket tab to open here -- the plain
 * "Ready to Ship" page, shared by every resolved order -- plus a
 * per-row "Copy ID" button so the operator can paste each id into
 * Shiprocket's own "Multiple Order IDs" filter one at a time. (A single
 * "copy every id at once" button is a natural follow-up, but Shiprocket's
 * exact accepted separator for that filter hasn't been confirmed yet --
 * deliberately not guessed at here.)
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

  function openReadyToShip() {
    window.open(SHIPROCKET_READY_TO_SHIP_URL, "_blank", "noopener,noreferrer")
  }

  async function copyId(id: string) {
    const copied = await copyToClipboard(id)
    if (copied) {
      toast.success(`Shiprocket Order ID ${id} copied.`)
    } else {
      toast.warning(`Shiprocket Order ID: ${id}`, {
        description: "Couldn't copy automatically -- copy this ID manually.",
      })
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
                    Shiprocket has no direct link to one specific order, so this opens its plain
                    Ready to Ship page -- copy each order&apos;s ID below and paste it into
                    Shiprocket&apos;s Multiple Order IDs filter to find it.
                  </span>
                  {found.length > 0 && (
                    <div className="border-border rounded-md border p-2">
                      <p className="mb-1 text-xs font-semibold">Found — copy each ID:</p>
                      <ul className="flex flex-col gap-1 text-xs">
                        {found.map((r) => (
                          <li
                            key={r.order_id}
                            className="flex items-center justify-between gap-2"
                          >
                            <span className="font-medium">
                              {rowsByOrderId.get(r.order_id)?.order_number ?? r.order_id}
                            </span>
                            <span className="flex items-center gap-1.5">
                              <span className="text-muted-foreground font-mono">
                                {r.shiprocket_order_id}
                              </span>
                              <Button
                                size="sm"
                                variant="outline"
                                className="h-6 px-2"
                                onClick={() =>
                                  r.shiprocket_order_id && void copyId(r.shiprocket_order_id)
                                }
                              >
                                <Copy className="size-3" />
                                Copy ID
                              </Button>
                            </span>
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
          <Button onClick={openReadyToShip} disabled={isLoading || found.length === 0}>
            <ExternalLink className="size-3.5" />
            Open Ready to Ship
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}

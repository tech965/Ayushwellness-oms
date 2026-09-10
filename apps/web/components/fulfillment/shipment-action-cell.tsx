"use client"

import * as React from "react"
import { useRouter } from "next/navigation"
import { ExternalLink } from "lucide-react"
import { toast } from "sonner"

import { ShiprocketOrderIdDialog } from "@/components/fulfillment/shiprocket-order-id-dialog"
import { Button } from "@/components/ui/button"
import { getApiErrorMessage } from "@/lib/api-client"
import { SHIPROCKET_READY_TO_SHIP_URL } from "@/lib/shiprocket"
import { useLocateShiprocketOrders } from "@/services/shipment-queue"
import { useRetryShopifySync } from "@/services/shipments"

export interface ShipmentActionCellProps {
  orderId: string
  shipmentId: string | null | undefined
  shipmentStatus: string | null | undefined
  shopifySyncStatus: string | null | undefined
  orderStatus: string
  fulfillmentStatus: string
  // The real, numeric Shiprocket order id for this order's (most recent)
  // shipment -- `null`/`undefined` whenever the OMS has no reliably-
  // stored Shiprocket order id for it (including "no shipment yet"). See
  // `app.services.shiprocket_service.shiprocket_order_id` on the backend
  // for exactly how/when this is computed.
  shiprocketOrderId: string | null | undefined
}

/** The Action column cell shared by "Orders Need Shipment" and "Confirmed
 * by Telecaller" -- one source of truth for "what button(s) make sense
 * for this row right now," so the two tables can never drift apart.
 *
 * "Ship Order"/"Process Shipment" open Shiprocket's plain "Ready to
 * Ship" page in a new tab, then show `ShiprocketOrderIdDialog` with the
 * order's real Shiprocket order id -- they never call any Shiprocket
 * create-shipment API from here. Shiprocket support confirmed there is
 * no supported deep-link URL for one specific order (an earlier version
 * of this tried `?order_ids={id}`; Shiprocket's own page silently
 * ignored it), so the id is handed to the operator via that dialog to
 * paste into Shiprocket's own "Multiple Order IDs" filter instead --
 * copying happens only from a direct click on the dialog's own "Copy
 * Order ID" button, never automatically right after `window.open()` or
 * the live lookup below (see that dialog's docstring for why). Shiprocket
 * may already have this order (e.g. via its own Shopify channel
 * connector, entirely independent of this OMS), so blindly creating a
 * shipment on click risked a real, confirmed duplicate-shipment bug.
 * When the OMS has no locally-stored Shiprocket order id for this row
 * (`shiprocketOrderId` is `null`/`undefined`), a click triggers a live,
 * bounded lookup (`useLocateShiprocketOrders` -- see
 * `app.services.shiprocket_service.locate_shiprocket_orders`) that
 * resolves the order's EXISTING Shiprocket order by exact identifier,
 * never a guess and never a create-shipment call; only if that also
 * finds nothing does the button say the id is unavailable.
 */
export function ShipmentActionCell({
  orderId,
  shipmentId,
  shipmentStatus,
  shopifySyncStatus,
  orderStatus,
  fulfillmentStatus,
  shiprocketOrderId,
}: ShipmentActionCellProps) {
  const router = useRouter()
  const retrySync = useRetryShopifySync(shipmentId ?? "")
  const locate = useLocateShiprocketOrders()
  const [dialogOpen, setDialogOpen] = React.useState(false)
  const [dialogOrderId, setDialogOrderId] = React.useState<string | null>(null)
  // Debounces a double-click into one `window.open`, not two tabs -- the
  // dialog itself staying open is not enough of a guard on its own,
  // since the button underneath it remains clickable.
  const [opening, setOpening] = React.useState(false)

  const noBlockingShipment = !shipmentStatus || shipmentStatus === "cancelled"
  const eligibleToShip =
    orderStatus === "confirmed" && fulfillmentStatus !== "fulfilled" && noBlockingShipment

  function showUnavailable(message?: string | null) {
    toast.error("Shiprocket order ID is unavailable for this order.", {
      description:
        message ??
        "The OMS doesn't have a stored Shiprocket order id for this order yet -- it hasn't " +
          "been pushed to Shiprocket from here, and hasn't been matched back from Shiprocket " +
          "either.",
    })
  }

  // Opens Shiprocket's plain "Ready to Ship" page and hands the id to
  // the operator via `ShiprocketOrderIdDialog` -- never an automatic
  // clipboard write here (see that dialog's docstring for exactly why).
  function openReadyToShipAndShowId(id: string) {
    setOpening(true)
    window.open(SHIPROCKET_READY_TO_SHIP_URL, "_blank", "noopener,noreferrer")
    setDialogOrderId(id)
    setDialogOpen(true)
    window.setTimeout(() => setOpening(false), 1000)
  }

  function openShiprocketOrder(e: React.MouseEvent) {
    e.stopPropagation()
    if (opening || locate.isPending) return
    if (shiprocketOrderId) {
      openReadyToShipAndShowId(shiprocketOrderId)
      return
    }
    // No locally-known Shiprocket order id yet -- ask the backend to
    // locate the EXISTING order live before giving up (never creates
    // one; see `useLocateShiprocketOrders`).
    locate.mutate([orderId], {
      onSuccess: (results) => {
        const result = results[0]
        if (result?.shiprocket_order_id) {
          openReadyToShipAndShowId(result.shiprocket_order_id)
        } else {
          showUnavailable(result?.message)
        }
      },
      onError: (error) => toast.error(getApiErrorMessage(error)),
    })
  }

  const isBusy = opening || locate.isPending

  const viewButton = (
    <Button
      size="sm"
      variant="outline"
      onClick={(e) => {
        e.stopPropagation()
        router.push(`/orders/${orderId}`)
      }}
    >
      View
    </Button>
  )

  const dialog = (
    <ShiprocketOrderIdDialog open={dialogOpen} onOpenChange={setDialogOpen} orderId={dialogOrderId} />
  )

  if (eligibleToShip) {
    return (
      <div className="flex items-center gap-1.5">
        <Button size="sm" disabled={isBusy} onClick={openShiprocketOrder}>
          <ExternalLink className="size-3.5" />
          {locate.isPending ? "Checking..." : "Ship Order"}
        </Button>
        {viewButton}
        {dialog}
      </div>
    )
  }

  // Shipment created but not yet picked up -- still needs AWB
  // assignment/pickup, done on Shiprocket's own order page now, not an
  // OMS-internal one.
  if (shipmentId && shipmentStatus === "pending") {
    return (
      <div className="flex items-center gap-1.5">
        <Button size="sm" variant="outline" disabled={isBusy} onClick={openShiprocketOrder}>
          <ExternalLink className="size-3.5" />
          {locate.isPending ? "Checking..." : "Process Shipment"}
        </Button>
        {viewButton}
        {dialog}
      </div>
    )
  }

  if (shipmentId && shopifySyncStatus === "failed") {
    return (
      <div className="flex items-center gap-1.5">
        <Button
          size="sm"
          variant="outline"
          disabled={retrySync.isPending}
          onClick={(e) => {
            e.stopPropagation()
            retrySync.mutate(undefined, {
              onSuccess: (shipment) => {
                if (shipment?.shopify_sync_status === "synced") {
                  toast.success("Shopify sync succeeded.")
                } else {
                  toast.warning("Shopify sync still failing — see the shipment for details.")
                }
              },
              onError: (error) => toast.error(getApiErrorMessage(error)),
            })
          }}
        >
          {retrySync.isPending ? "Retrying..." : "Retry Sync"}
        </Button>
        {viewButton}
      </div>
    )
  }

  return viewButton
}

"use client"

import * as React from "react"
import { useRouter } from "next/navigation"
import { ExternalLink } from "lucide-react"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import { getApiErrorMessage } from "@/lib/api-client"
import { useLocateShiprocketOrders } from "@/services/shipment-queue"
import { useRetryShopifySync } from "@/services/shipments"

export interface ShipmentActionCellProps {
  orderId: string
  shipmentId: string | null | undefined
  shipmentStatus: string | null | undefined
  shopifySyncStatus: string | null | undefined
  orderStatus: string
  fulfillmentStatus: string
  // The real Shiprocket order-details page for this order's (most
  // recent) shipment -- `null`/`undefined` whenever the OMS has no
  // reliably-stored Shiprocket order id for it (including "no shipment
  // yet"). See `app.services.shiprocket_service.shiprocket_order_url`
  // on the backend for exactly how/when this is computed.
  shiprocketOrderUrl: string | null | undefined
}

/** The Action column cell shared by "Orders Need Shipment" and "Confirmed
 * by Telecaller" -- one source of truth for "what button(s) make sense
 * for this row right now," so the two tables can never drift apart.
 *
 * "Ship Order"/"Process Shipment" open the real Shiprocket order-details
 * page in a new tab -- they never call any Shiprocket create-shipment
 * API from here. Shiprocket may already have this order (e.g. via its
 * own Shopify channel connector, entirely independent of this OMS), so
 * blindly creating a shipment on click risked a real, confirmed
 * duplicate-shipment bug; opening Shiprocket's own page instead lets the
 * operator review/assign courier/Ship Now there directly, using
 * whichever Shiprocket order already exists. When the OMS has no
 * locally-stored Shiprocket order id for this row (`shiprocketOrderUrl`
 * is `null`/`undefined`), a click triggers a live, bounded lookup
 * (`useLocateShiprocketOrders` -- see `app.services.shiprocket_service.
 * locate_shiprocket_orders`) that resolves the order's EXISTING
 * Shiprocket order by exact identifier, never a guess and never a
 * create-shipment call; only if that also finds nothing does the button
 * say the link is unavailable.
 */
export function ShipmentActionCell({
  orderId,
  shipmentId,
  shipmentStatus,
  shopifySyncStatus,
  orderStatus,
  fulfillmentStatus,
  shiprocketOrderUrl,
}: ShipmentActionCellProps) {
  const router = useRouter()
  const retrySync = useRetryShopifySync(shipmentId ?? "")
  const locate = useLocateShiprocketOrders()
  // Debounces a double-click into one open, not two tabs -- the click
  // itself is a read-only `window.open`, never a mutation, so this is
  // purely a UX guard, not a correctness one.
  const [opening, setOpening] = React.useState(false)

  const noBlockingShipment = !shipmentStatus || shipmentStatus === "cancelled"
  const eligibleToShip =
    orderStatus === "confirmed" && fulfillmentStatus !== "fulfilled" && noBlockingShipment

  function showUnavailable(message?: string | null) {
    toast.error("Shiprocket order link is unavailable for this order.", {
      description:
        message ??
        "The OMS doesn't have a stored Shiprocket order id for this order yet -- it hasn't " +
          "been pushed to Shiprocket from here, and hasn't been matched back from Shiprocket " +
          "either.",
    })
  }

  function openShiprocketOrder(e: React.MouseEvent) {
    e.stopPropagation()
    if (opening || locate.isPending) return
    if (shiprocketOrderUrl) {
      setOpening(true)
      window.open(shiprocketOrderUrl, "_blank", "noopener,noreferrer")
      window.setTimeout(() => setOpening(false), 1000)
      return
    }
    // No locally-known Shiprocket order id yet -- ask the backend to
    // locate the EXISTING order live before giving up (never creates
    // one; see `useLocateShiprocketOrders`).
    locate.mutate([orderId], {
      onSuccess: (results) => {
        const result = results[0]
        if (result?.shiprocket_order_url) {
          window.open(result.shiprocket_order_url, "_blank", "noopener,noreferrer")
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

  if (eligibleToShip) {
    return (
      <div className="flex items-center gap-1.5">
        <Button size="sm" disabled={isBusy} onClick={openShiprocketOrder}>
          <ExternalLink className="size-3.5" />
          {locate.isPending ? "Checking..." : "Ship Order"}
        </Button>
        {viewButton}
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

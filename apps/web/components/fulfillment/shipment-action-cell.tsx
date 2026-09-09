"use client"

import { useRouter } from "next/navigation"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import { getApiErrorMessage } from "@/lib/api-client"
import { useRetryShopifySync } from "@/services/shipments"

export interface ShipmentActionCellProps {
  orderId: string
  shipmentId: string | null | undefined
  shipmentStatus: string | null | undefined
  shopifySyncStatus: string | null | undefined
  orderStatus: string
  fulfillmentStatus: string
  onShip: (orderId: string) => void
  shipPending: boolean
}

/** The Action column cell shared by "Orders Need Shipment" and "Confirmed
 * by Telecaller" -- one source of truth for "what button(s) make sense
 * for this row right now," so the two tables can never show a "Ship
 * Order" button for an order that can't legally be shipped, or drift
 * apart on what "eligible" looks like between them.
 *
 * This is a CLIENT-SIDE approximation of `ShiprocketOperationsService.
 * _check_shippable` (mirrors its "confirmed, not fulfilled, no blocking
 * shipment" rule) purely to decide which buttons to render -- it is
 * NEVER the actual authority. Clicking "Ship Order" still goes through
 * the real, fully-validated backend endpoint every time, so a stale or
 * over-eager client guess here can only ever produce a clear rejection
 * toast, never an unsafe write.
 */
export function ShipmentActionCell({
  orderId,
  shipmentId,
  shipmentStatus,
  shopifySyncStatus,
  orderStatus,
  fulfillmentStatus,
  onShip,
  shipPending,
}: ShipmentActionCellProps) {
  const router = useRouter()
  const retrySync = useRetryShopifySync(shipmentId ?? "")

  const noBlockingShipment = !shipmentStatus || shipmentStatus === "cancelled"
  const eligibleToShip =
    orderStatus === "confirmed" && fulfillmentStatus !== "fulfilled" && noBlockingShipment

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
        <Button
          size="sm"
          disabled={shipPending}
          onClick={(e) => {
            e.stopPropagation()
            onShip(orderId)
          }}
        >
          {shipPending ? "Shipping..." : "Ship Order"}
        </Button>
        {viewButton}
      </div>
    )
  }

  // Shipment created but not yet picked up -- still needs AWB
  // assignment/pickup, which live on the shipment detail page. A direct
  // link there (not just "View" -> the order) is what removes the extra
  // click the original Fulfillment Queue's "Process Shipment" button
  // already avoided.
  if (shipmentId && shipmentStatus === "pending") {
    return (
      <div className="flex items-center gap-1.5">
        <Button
          size="sm"
          variant="outline"
          onClick={(e) => {
            e.stopPropagation()
            router.push(`/shipments/${shipmentId}`)
          }}
        >
          Process Shipment
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

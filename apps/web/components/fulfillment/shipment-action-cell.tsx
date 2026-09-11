"use client"

import * as React from "react"
import { useRouter } from "next/navigation"
import { ExternalLink } from "lucide-react"
import { toast } from "sonner"

import { ProcessShipmentDialog } from "@/components/fulfillment/process-shipment-dialog"
import { Button } from "@/components/ui/button"
import { getApiErrorMessage } from "@/lib/api-client"
import { useProcessExistingShipments } from "@/services/orders"
import { useRetryShopifySync } from "@/services/shipments"
import type { ProcessExistingShipmentResult } from "@/types/shipment"

export interface ShipmentActionCellProps {
  orderId: string
  shipmentId: string | null | undefined
  shipmentStatus: string | null | undefined
  shopifySyncStatus: string | null | undefined
  orderStatus: string
  fulfillmentStatus: string
}

/** The Action column cell shared by "Orders Need Shipment" and "Confirmed
 * by Telecaller" -- one source of truth for "what button(s) make sense
 * for this row right now," so the two tables can never drift apart.
 *
 * "Ship Order"/"Process Shipment" call `useProcessExistingShipments` --
 * the API equivalent of Shiprocket's own dashboard "Bulk Ship Orders"
 * action: resolve this order's EXISTING Shiprocket shipment (server-side,
 * via the unchanged `locate_shiprocket_orders`), assign it an AWB
 * (skipping if one is already on file), and show the outcome in
 * `ProcessShipmentDialog`. NEVER a Shiprocket create-shipment API call --
 * Shiprocket may already have this order (e.g. via its own Shopify
 * channel connector, entirely independent of this OMS), so blindly
 * creating a shipment on click risked a real, confirmed duplicate-
 * shipment bug.
 */
export function ShipmentActionCell({
  orderId,
  shipmentId,
  shipmentStatus,
  shopifySyncStatus,
  orderStatus,
  fulfillmentStatus,
}: ShipmentActionCellProps) {
  const router = useRouter()
  const retrySync = useRetryShopifySync(shipmentId ?? "")
  const processShipment = useProcessExistingShipments()
  const [dialogOpen, setDialogOpen] = React.useState(false)
  const [dialogResult, setDialogResult] = React.useState<ProcessExistingShipmentResult | null>(
    null
  )

  const noBlockingShipment = !shipmentStatus || shipmentStatus === "cancelled"
  const eligibleToShip =
    orderStatus === "confirmed" && fulfillmentStatus !== "fulfilled" && noBlockingShipment

  function handleProcessShipment(e: React.MouseEvent) {
    e.stopPropagation()
    setDialogResult(null)
    setDialogOpen(true)
    processShipment.mutate([orderId], {
      onSuccess: (data) => setDialogResult(data.results[0] ?? null),
      onError: (error) =>
        setDialogResult({
          order_id: orderId,
          order_number: null,
          status: "failed",
          shiprocket_shipment_id: null,
          shiprocket_order_id: null,
          awb: null,
          courier_name: null,
          reason: getApiErrorMessage(error),
        }),
    })
  }

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
    <ProcessShipmentDialog
      open={dialogOpen}
      onOpenChange={setDialogOpen}
      isPending={processShipment.isPending}
      result={dialogResult}
    />
  )

  if (eligibleToShip) {
    return (
      <div className="flex items-center gap-1.5">
        <Button size="sm" disabled={processShipment.isPending} onClick={handleProcessShipment}>
          <ExternalLink className="size-3.5" />
          {processShipment.isPending ? "Processing..." : "Ship Order"}
        </Button>
        {viewButton}
        {dialog}
      </div>
    )
  }

  // Shipment created but not yet picked up -- still needs AWB
  // assignment/pickup.
  if (shipmentId && shipmentStatus === "pending") {
    return (
      <div className="flex items-center gap-1.5">
        <Button
          size="sm"
          variant="outline"
          disabled={processShipment.isPending}
          onClick={handleProcessShipment}
        >
          <ExternalLink className="size-3.5" />
          {processShipment.isPending ? "Processing..." : "Process Shipment"}
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

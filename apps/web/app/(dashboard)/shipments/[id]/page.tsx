"use client"

import * as React from "react"
import type { ReactNode } from "react"
import Link from "next/link"
import { useParams } from "next/navigation"
import { toast } from "sonner"

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { PageHeader } from "@/components/shared/page-header"
import { QueryStates } from "@/components/shared/query-states"
import { StatusBadge } from "@/components/shared/status-badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { getApiErrorMessage } from "@/lib/api-client"
import { useAuth } from "@/lib/auth-context"
import { formatDateTime } from "@/lib/format"
import {
  useAssignAwb,
  useCancelShipment,
  useRequestPickup,
  useRefreshTracking,
  useRetryShopifySync,
  useShipment,
  useShipmentTimeline,
} from "@/services/shipments"

// A shipment Shiprocket hasn't picked up yet is the only state this OMS
// can safely offer to reverse -- once picked up, Shiprocket itself
// generally won't accept a cancellation, and the shipment may already be
// physically moving. Never offer a button that would just fail; see the
// "cannot be automatically reversed" message below instead. This is a
// client-side UX guess at Shiprocket's own real policy, not the actual
// authority -- the backend (`ShiprocketOperationsService.cancel_shipment`)
// only ever marks OMS state CANCELLED after Shiprocket's own API call
// actually succeeds, so this gate can only ever hide a button too early,
// never let through an unsafe write.
const CANCELLABLE_STATUSES = new Set(["pending"])

export default function ShipmentDetailPage() {
  const params = useParams<{ id: string }>()
  const shipmentId = params.id
  const { hasPermission } = useAuth()
  const [cancelConfirmOpen, setCancelConfirmOpen] = React.useState(false)

  const shipmentQuery = useShipment(shipmentId)
  const timelineQuery = useShipmentTimeline(shipmentId)

  const assignAwb = useAssignAwb(shipmentId)
  const cancelShipment = useCancelShipment(shipmentId)
  const requestPickup = useRequestPickup(shipmentId)
  const refreshTracking = useRefreshTracking(shipmentId)
  const retryShopifySync = useRetryShopifySync(shipmentId)

  const canOperate = hasPermission("shipments.update")
  const anyActionPending =
    assignAwb.isPending ||
    cancelShipment.isPending ||
    requestPickup.isPending ||
    refreshTracking.isPending ||
    retryShopifySync.isPending

  const mutationOpts = (successMessage: string) => ({
    onSuccess: () => toast.success(successMessage),
    onError: (error: unknown) => toast.error(getApiErrorMessage(error)),
  })

  return (
    <>
      <PageHeader
        title={
          shipmentQuery.data?.awb ? `Shipment ${shipmentQuery.data.awb}` : "Shipment"
        }
        description={`ID: ${shipmentId}`}
        actions={
          canOperate && (
            <div className="flex flex-wrap gap-2">
              <Button
                size="sm"
                variant="outline"
                disabled={anyActionPending || Boolean(shipmentQuery.data?.awb)}
                onClick={() => assignAwb.mutate(undefined, mutationOpts("AWB assigned."))}
              >
                Assign AWB
              </Button>
              <Button
                size="sm"
                variant="outline"
                disabled={anyActionPending}
                onClick={() =>
                  requestPickup.mutate(undefined, mutationOpts("Pickup requested."))
                }
              >
                Request Pickup
              </Button>
              <Button
                size="sm"
                variant="outline"
                disabled={anyActionPending || !shipmentQuery.data?.awb}
                onClick={() =>
                  refreshTracking.mutate(undefined, mutationOpts("Tracking refreshed."))
                }
              >
                Refresh Tracking
              </Button>
              {shipmentQuery.data && shipmentQuery.data.current_status !== "cancelled" && (
                CANCELLABLE_STATUSES.has(shipmentQuery.data.current_status) ? (
                  <Button
                    size="sm"
                    variant="destructive"
                    disabled={anyActionPending}
                    onClick={() => setCancelConfirmOpen(true)}
                  >
                    Cancel Shipment
                  </Button>
                ) : (
                  <span className="text-muted-foreground self-center text-xs">
                    Shipment cannot be automatically reversed at this stage. Contact Admin.
                  </span>
                )
              )}
              {shipmentQuery.data?.shopify_sync_status === "failed" && (
                <Button
                  size="sm"
                  variant="outline"
                  disabled={anyActionPending}
                  onClick={() =>
                    retryShopifySync.mutate(undefined, {
                      onSuccess: (shipment) => {
                        if (shipment?.shopify_sync_status === "synced") {
                          toast.success("Shopify sync succeeded.")
                        } else {
                          toast.warning("Shopify sync still failing — see the error below.")
                        }
                      },
                      onError: (error) => toast.error(getApiErrorMessage(error)),
                    })
                  }
                >
                  Retry Shopify Sync
                </Button>
              )}
            </div>
          )
        }
      />
      <QueryStates
        isLoading={shipmentQuery.isLoading}
        isError={shipmentQuery.isError}
        error={shipmentQuery.error}
        data={shipmentQuery.data}
        onRetry={() => void shipmentQuery.refetch()}
      >
        {(shipment) => (
          <div className="flex flex-col gap-4">
            <Card>
              <CardHeader className="flex flex-row items-center gap-2">
                <StatusBadge domain="shipment" status={shipment.current_status} />
                <StatusBadge domain="shipment_delay" status={shipment.delay_status} />
                {shipment.ndr_status && (
                  <StatusBadge domain="ndr" status={shipment.ndr_status} />
                )}
                {shipment.rto_status && (
                  <StatusBadge domain="rto" status={shipment.rto_status} />
                )}
                <StatusBadge domain="shopify_sync" status={shipment.shopify_sync_status} />
              </CardHeader>
              {shipment.shopify_sync_status === "failed" && shipment.shopify_sync_error && (
                <CardContent className="border-border border-b pt-0 pb-4">
                  <p className="text-destructive text-sm">
                    Shopify sync failed: {shipment.shopify_sync_error}
                  </p>
                </CardContent>
              )}
              <CardContent className="grid grid-cols-2 gap-4 sm:grid-cols-4">
                <Stat
                  label="Order"
                  value={
                    <Link
                      href={`/orders/${shipment.order_id}`}
                      className="text-primary hover:underline"
                    >
                      View order
                    </Link>
                  }
                />
                <Stat label="AWB" value={shipment.awb ?? "—"} />
                <Stat
                  label="Shopify fulfillment"
                  value={shipment.shopify_fulfillment_id ?? "—"}
                />
                <Stat
                  label="Source"
                  value={
                    shipment.source_system === "shiprocket"
                      ? "Shiprocket"
                      : (shipment.source_system ?? "Manual")
                  }
                />
                <Stat label="Current location" value={shipment.current_location ?? "—"} />
                <Stat
                  label="Expected delivery"
                  value={
                    shipment.expected_delivery_date
                      ? formatDateTime(shipment.expected_delivery_date)
                      : "—"
                  }
                />
                <Stat
                  label="Actual delivery"
                  value={
                    shipment.actual_delivery_date
                      ? formatDateTime(shipment.actual_delivery_date)
                      : "—"
                  }
                />
                <Stat
                  label="Pickup date"
                  value={
                    shipment.pickup_date ? formatDateTime(shipment.pickup_date) : "—"
                  }
                />
                <Stat
                  label="Last update"
                  value={
                    shipment.last_tracking_update_at
                      ? formatDateTime(shipment.last_tracking_update_at)
                      : "—"
                  }
                />
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>Tracking timeline</CardTitle>
              </CardHeader>
              <CardContent>
                <QueryStates
                  isLoading={timelineQuery.isLoading}
                  isError={timelineQuery.isError}
                  error={timelineQuery.error}
                  data={timelineQuery.data}
                  onRetry={() => void timelineQuery.refetch()}
                  isEmpty={(events) => events.length === 0}
                  emptyTitle="No tracking events yet"
                  emptyDescription="Assign an AWB and refresh tracking to pull events from Shiprocket."
                >
                  {(events) => (
                    <ol className="flex flex-col gap-3">
                      {events.map((event) => (
                        <li
                          key={event.id}
                          className="border-border flex gap-3 border-l-2 pl-3"
                        >
                          <div className="flex flex-col">
                            <span className="text-sm font-medium">
                              {event.description ?? event.status}
                            </span>
                            <span className="text-muted-foreground text-xs">
                              {formatDateTime(event.event_timestamp)}
                              {event.location ? ` · ${event.location}` : ""}
                              {event.courier_name ? ` · ${event.courier_name}` : ""}
                            </span>
                          </div>
                        </li>
                      ))}
                    </ol>
                  )}
                </QueryStates>
              </CardContent>
            </Card>
          </div>
        )}
      </QueryStates>

      <AlertDialog open={cancelConfirmOpen} onOpenChange={setCancelConfirmOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Are you sure you want to cancel this shipment?</AlertDialogTitle>
            <AlertDialogDescription>
              This will cancel the shipment in OMS and, where supported and still cancellable,
              cancel the corresponding Shiprocket shipment. The shipment record and its tracking
              history are kept — nothing is deleted, and this action is logged. If Shiprocket
              rejects the cancellation (e.g. it has already progressed), nothing changes and
              you&apos;ll see the reason.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Keep Shipment</AlertDialogCancel>
            <AlertDialogAction
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
              disabled={cancelShipment.isPending}
              onClick={(e) => {
                e.preventDefault()
                cancelShipment.mutate(undefined, {
                  onSuccess: () => {
                    toast.success("Shipment cancelled.")
                    setCancelConfirmOpen(false)
                  },
                  onError: (error) => toast.error(getApiErrorMessage(error)),
                })
              }}
            >
              {cancelShipment.isPending ? "Cancelling..." : "Cancel Shipment"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  )
}

function Stat({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div>
      <p className="text-muted-foreground text-xs">{label}</p>
      <p className="text-sm font-medium">{value}</p>
    </div>
  )
}

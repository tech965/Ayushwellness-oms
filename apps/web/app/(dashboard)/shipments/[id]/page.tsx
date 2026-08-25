"use client"

import type { ReactNode } from "react"
import Link from "next/link"
import { useParams } from "next/navigation"
import { toast } from "sonner"

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
  useShipment,
  useShipmentTimeline,
} from "@/services/shipments"

export default function ShipmentDetailPage() {
  const params = useParams<{ id: string }>()
  const shipmentId = params.id
  const { hasPermission } = useAuth()

  const shipmentQuery = useShipment(shipmentId)
  const timelineQuery = useShipmentTimeline(shipmentId)

  const assignAwb = useAssignAwb(shipmentId)
  const cancelShipment = useCancelShipment(shipmentId)
  const requestPickup = useRequestPickup(shipmentId)
  const refreshTracking = useRefreshTracking(shipmentId)

  const canOperate = hasPermission("shipments.update")
  const anyActionPending =
    assignAwb.isPending ||
    cancelShipment.isPending ||
    requestPickup.isPending ||
    refreshTracking.isPending

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
              <Button
                size="sm"
                variant="destructive"
                disabled={anyActionPending}
                onClick={() =>
                  cancelShipment.mutate(undefined, mutationOpts("Shipment cancelled."))
                }
              >
                Cancel Shipment
              </Button>
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
              </CardHeader>
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

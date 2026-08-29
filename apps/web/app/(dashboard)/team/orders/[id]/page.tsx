"use client"

import * as React from "react"
import { useParams } from "next/navigation"
import { Repeat } from "lucide-react"
import { toast } from "sonner"

import { PageHeader } from "@/components/shared/page-header"
import { QueryStates } from "@/components/shared/query-states"
import { StatusBadge } from "@/components/shared/status-badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Textarea } from "@/components/ui/textarea"
import { getApiErrorMessage } from "@/lib/api-client"
import { formatDateTime, formatMoney } from "@/lib/format"
import {
  useReassignOrder,
  useTeamOrder,
  useTeamOrderCallHistory,
  useTeamTelecallers,
} from "@/services/team"

function SummaryStat({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div>
      <p className="text-muted-foreground text-xs font-medium tracking-wide uppercase">
        {label}
      </p>
      <p className="text-sm font-medium">{value}</p>
    </div>
  )
}

export default function TeamOrderDetailPage() {
  const params = useParams<{ id: string }>()
  const orderId = params.id

  const orderQuery = useTeamOrder(orderId)
  const historyQuery = useTeamOrderCallHistory(orderId)
  const telecallersQuery = useTeamTelecallers()
  const reassign = useReassignOrder()

  const [reassignOpen, setReassignOpen] = React.useState(false)
  const [newTelecallerId, setNewTelecallerId] = React.useState("")
  const [reason, setReason] = React.useState("")

  function handleReassign() {
    if (!newTelecallerId || !reason) return
    reassign.mutate(
      { order_id: orderId, new_telecaller_id: newTelecallerId, reason },
      {
        onSuccess: () => {
          toast.success("Order reassigned.")
          setReassignOpen(false)
          setReason("")
          setNewTelecallerId("")
        },
        onError: (error) => toast.error(getApiErrorMessage(error)),
      }
    )
  }

  return (
    <>
      <PageHeader
        title={orderQuery.data ? `Order ${orderQuery.data.order_number}` : "Order"}
        backHref="/team/orders/unfulfilled"
        backLabel="Back to Unfulfilled Orders"
      />
      <QueryStates
        isLoading={orderQuery.isLoading}
        isError={orderQuery.isError}
        error={orderQuery.error}
        data={orderQuery.data}
        onRetry={() => void orderQuery.refetch()}
      >
        {(order) => (
          <div className="flex flex-col gap-4">
            <div className="grid gap-4 lg:grid-cols-2">
              <Card>
                <CardHeader>
                  <CardTitle>Order</CardTitle>
                </CardHeader>
                <CardContent className="grid grid-cols-2 gap-4">
                  <SummaryStat label="Customer" value={order.customer_name ?? "—"} />
                  <SummaryStat label="Phone" value={order.customer_phone ?? "—"} />
                  <SummaryStat label="Product" value={order.item_summary ?? "—"} />
                  <SummaryStat label="Amount" value={formatMoney(order.total_amount)} />
                  <SummaryStat
                    label="Payment"
                    value={<StatusBadge domain="payment" status={order.payment_type} />}
                  />
                  <SummaryStat
                    label="Fulfillment"
                    value={
                      <StatusBadge
                        domain="fulfillment"
                        status={order.fulfillment_status}
                      />
                    }
                  />
                </CardContent>
              </Card>

              <Card>
                <CardHeader className="flex flex-row items-center justify-between gap-2">
                  <CardTitle>Assignment</CardTitle>
                  {order.assigned_to && (
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => setReassignOpen(true)}
                    >
                      <Repeat className="size-4" />
                      Reassign
                    </Button>
                  )}
                </CardHeader>
                <CardContent className="grid grid-cols-2 gap-4">
                  <SummaryStat
                    label="Assigned Telecaller"
                    value={order.assigned_to_name ?? "Unassigned"}
                  />
                  <SummaryStat
                    label="Call Status"
                    value={
                      <StatusBadge
                        domain="telecalling"
                        status={order.call_status ?? "not_called"}
                      />
                    }
                  />
                  <SummaryStat label="Attempts" value={order.attempt_count} />
                  <SummaryStat
                    label="Next Follow-up"
                    value={
                      order.next_follow_up_at
                        ? formatDateTime(order.next_follow_up_at)
                        : "—"
                    }
                  />
                </CardContent>
              </Card>
            </div>

            <Card>
              <CardHeader>
                <CardTitle>Call History</CardTitle>
              </CardHeader>
              <CardContent>
                <QueryStates
                  isLoading={historyQuery.isLoading}
                  isError={historyQuery.isError}
                  error={historyQuery.error}
                  data={historyQuery.data}
                  onRetry={() => void historyQuery.refetch()}
                  isEmpty={(events) => events.length === 0}
                  emptyTitle="No calls logged yet"
                >
                  {(attempts) => (
                    <ol className="flex flex-col gap-4">
                      {attempts.map((attempt) => (
                        <li
                          key={attempt.id}
                          className="border-border border-b pb-4 last:border-0 last:pb-0"
                        >
                          <div className="flex items-center justify-between gap-2">
                            <span className="text-sm font-semibold">
                              Attempt #{attempt.attempt_number}
                            </span>
                            <StatusBadge domain="telecalling" status={attempt.outcome} />
                          </div>
                          <p className="text-muted-foreground mt-0.5 text-xs">
                            {formatDateTime(attempt.attempted_at)}
                          </p>
                          {attempt.notes && (
                            <p className="mt-1 text-sm">{attempt.notes}</p>
                          )}
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

      <Dialog open={reassignOpen} onOpenChange={setReassignOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Reassign Order</DialogTitle>
            <DialogDescription>
              Move this order to a different telecaller. The previous assignment is kept
              in history, never deleted.
            </DialogDescription>
          </DialogHeader>
          <div className="flex flex-col gap-3">
            <Select value={newTelecallerId} onValueChange={setNewTelecallerId}>
              <SelectTrigger>
                <SelectValue placeholder="Select telecaller" />
              </SelectTrigger>
              <SelectContent>
                {telecallersQuery.data?.map((t) => (
                  <SelectItem key={t.telecaller_id} value={t.telecaller_id}>
                    {t.telecaller_name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Textarea
              placeholder="Reason for reassignment"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
            />
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setReassignOpen(false)}>
              Cancel
            </Button>
            <Button
              onClick={handleReassign}
              disabled={reassign.isPending || !newTelecallerId || !reason}
            >
              {reassign.isPending ? "Reassigning..." : "Reassign"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}

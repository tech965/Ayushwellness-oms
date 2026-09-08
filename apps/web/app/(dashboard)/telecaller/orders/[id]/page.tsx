"use client"

import * as React from "react"
import { useParams } from "next/navigation"
import {
  CalendarClock,
  CheckCircle2,
  PackageCheck,
  Pencil,
  PhoneCall,
  Trash2,
  Undo2,
  XCircle,
} from "lucide-react"
import { toast } from "sonner"

import { PageHeader } from "@/components/shared/page-header"
import { QueryStates } from "@/components/shared/query-states"
import { StatusBadge } from "@/components/shared/status-badge"
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
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Textarea } from "@/components/ui/textarea"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { getApiErrorMessage } from "@/lib/api-client"
import { formatDateTime, formatMoney } from "@/lib/format"
import {
  useCallHistory,
  useConfirmOrder,
  useDeleteCallAttempt,
  useEditCallAttempt,
  useLogCall,
  useMyOrder,
  useScheduleFollowUp,
  useUnconfirmOrder,
} from "@/services/telecaller"
import {
  CALL_OUTCOME_OPTIONS,
  type CallAttempt,
  type TelecallingStatus,
} from "@/types/telecalling"

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

/** ISO datetime -> the local-time value a `datetime-local` input expects
 * ("YYYY-MM-DDTHH:mm") — used only to pre-fill the Edit dialog with an
 * existing attempt's follow-up date; a fresh Log Call always starts blank
 * so this conversion was never needed there.
 */
function toDatetimeLocalValue(iso: string): string {
  const date = new Date(iso)
  const offsetMs = date.getTimezoneOffset() * 60_000
  return new Date(date.getTime() - offsetMs).toISOString().slice(0, 16)
}

export default function TelecallerOrderDetailPage() {
  const params = useParams<{ id: string }>()
  const orderId = params.id

  const orderQuery = useMyOrder(orderId)
  const historyQuery = useCallHistory(orderId)
  const logCall = useLogCall(orderId)
  const scheduleFollowUp = useScheduleFollowUp(orderId)
  const editCallAttempt = useEditCallAttempt(orderId)
  const deleteCallAttempt = useDeleteCallAttempt(orderId)
  const confirmOrder = useConfirmOrder(orderId)
  const unconfirmOrder = useUnconfirmOrder(orderId)

  const [confirmOrderOpen, setConfirmOrderOpen] = React.useState(false)
  const [unconfirmOrderOpen, setUnconfirmOrderOpen] = React.useState(false)

  function handleConfirmOrder() {
    confirmOrder.mutate(undefined, {
      onSuccess: () => {
        toast.success("Order confirmed — ready for shipment.")
        setConfirmOrderOpen(false)
      },
      onError: (error) => {
        toast.error(getApiErrorMessage(error))
        setConfirmOrderOpen(false)
      },
    })
  }

  function handleUnconfirmOrder() {
    unconfirmOrder.mutate(undefined, {
      onSuccess: () => {
        toast.success("Order reverted to pending.")
        setUnconfirmOrderOpen(false)
      },
      onError: (error) => {
        toast.error(getApiErrorMessage(error))
        setUnconfirmOrderOpen(false)
      },
    })
  }

  const [logCallOpen, setLogCallOpen] = React.useState(false)
  const [followUpOpen, setFollowUpOpen] = React.useState(false)
  const [outcome, setOutcome] = React.useState<TelecallingStatus>("connected")
  const [notes, setNotes] = React.useState("")
  const [nextFollowUp, setNextFollowUp] = React.useState("")
  const [followUpOnly, setFollowUpOnly] = React.useState("")

  const [editTarget, setEditTarget] = React.useState<CallAttempt | null>(null)
  const [editOutcome, setEditOutcome] = React.useState<TelecallingStatus>("connected")
  const [editNotes, setEditNotes] = React.useState("")
  const [editFollowUp, setEditFollowUp] = React.useState("")
  const [deleteTarget, setDeleteTarget] = React.useState<CallAttempt | null>(null)

  function openEdit(attempt: CallAttempt) {
    setEditTarget(attempt)
    setEditOutcome(attempt.outcome)
    setEditNotes(attempt.notes ?? "")
    setEditFollowUp(
      attempt.next_follow_up_at ? toDatetimeLocalValue(attempt.next_follow_up_at) : ""
    )
  }

  function handleEditSave() {
    if (!editTarget) return
    editCallAttempt.mutate(
      {
        attemptId: editTarget.id,
        input: {
          outcome: editOutcome,
          notes: editNotes || undefined,
          next_follow_up_at: editFollowUp
            ? new Date(editFollowUp).toISOString()
            : undefined,
        },
      },
      {
        onSuccess: () => {
          toast.success("Call attempt updated.")
          setEditTarget(null)
        },
        onError: (error) => toast.error(getApiErrorMessage(error)),
      }
    )
  }

  function handleConfirmDelete() {
    if (!deleteTarget) return
    deleteCallAttempt.mutate(deleteTarget.id, {
      onSuccess: () => {
        toast.success("Call attempt deleted.")
        setDeleteTarget(null)
      },
      onError: (error) => {
        toast.error(getApiErrorMessage(error))
        setDeleteTarget(null)
      },
    })
  }

  function handleLogCall() {
    logCall.mutate(
      {
        outcome,
        notes: notes || undefined,
        next_follow_up_at: nextFollowUp
          ? new Date(nextFollowUp).toISOString()
          : undefined,
      },
      {
        onSuccess: () => {
          toast.success("Call logged.")
          setLogCallOpen(false)
          setNotes("")
          setNextFollowUp("")
        },
        onError: (error) => toast.error(getApiErrorMessage(error)),
      }
    )
  }

  function handleScheduleFollowUp() {
    if (!followUpOnly) return
    scheduleFollowUp.mutate(new Date(followUpOnly).toISOString(), {
      onSuccess: () => {
        toast.success("Follow-up scheduled.")
        setFollowUpOpen(false)
        setFollowUpOnly("")
      },
      onError: (error) => toast.error(getApiErrorMessage(error)),
    })
  }

  function quickLog(quickOutcome: TelecallingStatus) {
    logCall.mutate(
      { outcome: quickOutcome },
      {
        onSuccess: () => toast.success("Call outcome recorded."),
        onError: (error) => toast.error(getApiErrorMessage(error)),
      }
    )
  }

  return (
    <>
      <PageHeader
        title={orderQuery.data ? `Order ${orderQuery.data.order_number}` : "Order"}
        backHref="/telecaller/orders"
        backLabel="Back to My Orders"
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
                  <CardTitle>Customer & Order</CardTitle>
                </CardHeader>
                <CardContent className="grid grid-cols-2 gap-4">
                  <SummaryStat label="Customer" value={order.customer_name ?? "—"} />
                  <SummaryStat label="Phone" value={order.customer_phone ?? "—"} />
                  <SummaryStat
                    label="Address"
                    value={
                      order.shipping_address
                        ? [
                            order.shipping_address.line1,
                            order.shipping_address.city,
                            order.shipping_address.state,
                            order.shipping_address.pin_code,
                          ]
                            .filter(Boolean)
                            .join(", ")
                        : "—"
                    }
                  />
                  <SummaryStat label="Product" value={order.item_summary ?? "—"} />
                  <SummaryStat label="Amount" value={formatMoney(order.total_amount)} />
                  <SummaryStat
                    label="Payment Type"
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
                  <SummaryStat
                    label="Order Status"
                    value={<StatusBadge domain="order" status={order.status} />}
                  />
                </CardContent>
                {order.status === "pending" && (
                  <CardContent className="border-border border-t pt-4">
                    <Button size="sm" onClick={() => setConfirmOrderOpen(true)}>
                      <PackageCheck className="size-4" />
                      Confirm Order for Shipment
                    </Button>
                    <p className="text-muted-foreground mt-1.5 text-xs">
                      Separate from call outcome — this marks the order ready for the
                      shipment team and does not log a call.
                    </p>
                  </CardContent>
                )}
                {order.status === "confirmed" && (
                  <CardContent className="border-border border-t pt-4">
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => setUnconfirmOrderOpen(true)}
                    >
                      <Undo2 className="size-4" />
                      Revert to Pending
                    </Button>
                    <p className="text-muted-foreground mt-1.5 text-xs">
                      Confirmed and ready for shipment. Only revertible if fulfillment hasn&apos;t
                      created a shipment for it yet.
                    </p>
                  </CardContent>
                )}
              </Card>

              <Card>
                <CardHeader>
                  <CardTitle>Call Management</CardTitle>
                </CardHeader>
                <CardContent className="flex flex-col gap-4">
                  <div className="grid grid-cols-2 gap-4">
                    <SummaryStat
                      label="Current Status"
                      value={
                        <StatusBadge
                          domain="telecalling"
                          status={order.call_status ?? "not_called"}
                        />
                      }
                    />
                    <SummaryStat label="Attempt Count" value={order.attempt_count} />
                    <SummaryStat
                      label="Last Attempt"
                      value={
                        order.last_attempt_at
                          ? formatDateTime(order.last_attempt_at)
                          : "—"
                      }
                    />
                    <SummaryStat
                      label="Next Follow-up"
                      value={
                        order.next_follow_up_at
                          ? formatDateTime(order.next_follow_up_at)
                          : "—"
                      }
                    />
                  </div>

                  <div className="flex flex-wrap gap-2">
                    <Button size="sm" onClick={() => setLogCallOpen(true)}>
                      <PhoneCall className="size-4" />
                      Log Call
                    </Button>
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => setFollowUpOpen(true)}
                    >
                      <CalendarClock className="size-4" />
                      Schedule Follow-up
                    </Button>
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={logCall.isPending}
                      onClick={() => quickLog("confirmed")}
                    >
                      <CheckCircle2 className="size-4" />
                      Mark Confirmed
                    </Button>
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={logCall.isPending}
                      onClick={() => quickLog("not_interested")}
                    >
                      <XCircle className="size-4" />
                      Mark Not Interested
                    </Button>
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={logCall.isPending}
                      onClick={() => quickLog("cancelled")}
                    >
                      <XCircle className="size-4" />
                      Mark Cancelled
                    </Button>
                  </div>
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
                  emptyDescription="Log your first call attempt above."
                >
                  {(attempts) => (
                    <ol className="flex flex-col gap-4">
                      {attempts.map((attempt) => (
                        <li
                          key={attempt.id}
                          className="border-border border-b pb-4 last:border-0 last:pb-0"
                        >
                          <div className="flex items-center justify-between gap-2">
                            <span className="flex items-center gap-1.5 text-sm font-semibold">
                              Attempt #{attempt.attempt_number}
                              {attempt.is_edited && (
                                <span className="text-muted-foreground text-xs font-normal">
                                  (edited)
                                </span>
                              )}
                            </span>
                            <div className="flex items-center gap-1">
                              <StatusBadge
                                domain="telecalling"
                                status={attempt.outcome}
                              />
                              <Tooltip>
                                <TooltipTrigger asChild>
                                  <Button
                                    variant="ghost"
                                    size="icon-sm"
                                    aria-label="Edit call attempt"
                                    onClick={() => openEdit(attempt)}
                                  >
                                    <Pencil />
                                  </Button>
                                </TooltipTrigger>
                                <TooltipContent>Edit</TooltipContent>
                              </Tooltip>
                              <Tooltip>
                                <TooltipTrigger asChild>
                                  <Button
                                    variant="ghost"
                                    size="icon-sm"
                                    className="text-destructive hover:bg-destructive/10 hover:text-destructive"
                                    aria-label="Delete call attempt"
                                    onClick={() => setDeleteTarget(attempt)}
                                  >
                                    <Trash2 />
                                  </Button>
                                </TooltipTrigger>
                                <TooltipContent>Delete</TooltipContent>
                              </Tooltip>
                            </div>
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

      <Dialog open={logCallOpen} onOpenChange={setLogCallOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Log Call</DialogTitle>
            <DialogDescription>
              Record the outcome of this call attempt.
            </DialogDescription>
          </DialogHeader>
          <div className="flex flex-col gap-3">
            <Select
              value={outcome}
              onValueChange={(v) => setOutcome(v as TelecallingStatus)}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {CALL_OUTCOME_OPTIONS.map((o) => (
                  <SelectItem key={o.value} value={o.value}>
                    {o.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Textarea
              placeholder="Notes (optional)"
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
            />
            <div>
              <label className="text-muted-foreground mb-1 block text-xs">
                Next follow-up (optional)
              </label>
              <Input
                type="datetime-local"
                value={nextFollowUp}
                onChange={(e) => setNextFollowUp(e.target.value)}
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setLogCallOpen(false)}>
              Cancel
            </Button>
            <Button onClick={handleLogCall} disabled={logCall.isPending}>
              {logCall.isPending ? "Saving..." : "Save"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={followUpOpen} onOpenChange={setFollowUpOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Schedule Follow-up</DialogTitle>
            <DialogDescription>
              Set the next follow-up date/time without logging a new call.
            </DialogDescription>
          </DialogHeader>
          <Input
            type="datetime-local"
            value={followUpOnly}
            onChange={(e) => setFollowUpOnly(e.target.value)}
          />
          <DialogFooter>
            <Button variant="outline" onClick={() => setFollowUpOpen(false)}>
              Cancel
            </Button>
            <Button
              onClick={handleScheduleFollowUp}
              disabled={scheduleFollowUp.isPending || !followUpOnly}
            >
              {scheduleFollowUp.isPending ? "Saving..." : "Schedule"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog
        open={editTarget !== null}
        onOpenChange={(open) => !open && setEditTarget(null)}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              Edit Attempt {editTarget ? `#${editTarget.attempt_number}` : ""}
            </DialogTitle>
            <DialogDescription>
              Correct the outcome or notes for this call.
            </DialogDescription>
          </DialogHeader>
          <div className="flex flex-col gap-3">
            <Select
              value={editOutcome}
              onValueChange={(v) => setEditOutcome(v as TelecallingStatus)}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {CALL_OUTCOME_OPTIONS.map((o) => (
                  <SelectItem key={o.value} value={o.value}>
                    {o.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Textarea
              placeholder="Notes (optional)"
              value={editNotes}
              onChange={(e) => setEditNotes(e.target.value)}
            />
            <div>
              <label className="text-muted-foreground mb-1 block text-xs">
                Next follow-up (optional)
              </label>
              <Input
                type="datetime-local"
                value={editFollowUp}
                onChange={(e) => setEditFollowUp(e.target.value)}
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setEditTarget(null)}>
              Cancel
            </Button>
            <Button onClick={handleEditSave} disabled={editCallAttempt.isPending}>
              {editCallAttempt.isPending ? "Saving..." : "Save"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <AlertDialog
        open={deleteTarget !== null}
        onOpenChange={(open) => !open && setDeleteTarget(null)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete this call attempt?</AlertDialogTitle>
            <AlertDialogDescription>
              This call history entry will be permanently removed.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <Button
              variant="destructive"
              onClick={handleConfirmDelete}
              disabled={deleteCallAttempt.isPending}
            >
              {deleteCallAttempt.isPending ? "Deleting..." : "Delete"}
            </Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <AlertDialog open={confirmOrderOpen} onOpenChange={setConfirmOrderOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Confirm this order?</AlertDialogTitle>
            <AlertDialogDescription>
              This marks the order ready for shipment. It does not create a shipment and does
              not change the call status.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <Button onClick={handleConfirmOrder} disabled={confirmOrder.isPending}>
              {confirmOrder.isPending ? "Confirming..." : "Confirm Order"}
            </Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <AlertDialog open={unconfirmOrderOpen} onOpenChange={setUnconfirmOrderOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Revert this order to pending?</AlertDialogTitle>
            <AlertDialogDescription>
              Undoes the confirmation — the order leaves the shipment queue and goes back to
              Pending. Only possible if fulfillment hasn&apos;t created a shipment for it yet. This
              does not change the call status.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <Button onClick={handleUnconfirmOrder} disabled={unconfirmOrder.isPending}>
              {unconfirmOrder.isPending ? "Reverting..." : "Revert Order"}
            </Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  )
}

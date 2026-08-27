"use client"

import * as React from "react"
import Link from "next/link"
import { useParams } from "next/navigation"
import { ArrowLeft, CalendarClock, CheckCircle2, PhoneCall, XCircle } from "lucide-react"
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
import { Input } from "@/components/ui/input"
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
  useCallHistory,
  useLogCall,
  useMyOrder,
  useScheduleFollowUp,
} from "@/services/telecaller"
import { CALL_OUTCOME_OPTIONS, type TelecallingStatus } from "@/types/telecalling"

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

export default function TelecallerOrderDetailPage() {
  const params = useParams<{ id: string }>()
  const orderId = params.id

  const orderQuery = useMyOrder(orderId)
  const historyQuery = useCallHistory(orderId)
  const logCall = useLogCall(orderId)
  const scheduleFollowUp = useScheduleFollowUp(orderId)

  const [logCallOpen, setLogCallOpen] = React.useState(false)
  const [followUpOpen, setFollowUpOpen] = React.useState(false)
  const [outcome, setOutcome] = React.useState<TelecallingStatus>("connected")
  const [notes, setNotes] = React.useState("")
  const [nextFollowUp, setNextFollowUp] = React.useState("")
  const [followUpOnly, setFollowUpOnly] = React.useState("")

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
        actions={
          <Button variant="outline" size="sm" asChild>
            <Link href="/telecaller/orders">
              <ArrowLeft className="size-4" />
              Back to My Orders
            </Link>
          </Button>
        }
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
                </CardContent>
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
    </>
  )
}

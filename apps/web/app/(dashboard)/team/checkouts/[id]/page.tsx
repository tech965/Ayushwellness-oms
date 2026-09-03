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
  useReassignCheckout,
  useTeamCheckout,
  useTeamCheckoutCallHistory,
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

export default function TeamCheckoutDetailPage() {
  const params = useParams<{ id: string }>()
  const checkoutId = params.id

  const checkoutQuery = useTeamCheckout(checkoutId)
  const historyQuery = useTeamCheckoutCallHistory(checkoutId)
  const telecallersQuery = useTeamTelecallers()
  const reassign = useReassignCheckout()

  const [reassignOpen, setReassignOpen] = React.useState(false)
  const [newTelecallerId, setNewTelecallerId] = React.useState("")
  const [reason, setReason] = React.useState("")

  function handleReassign() {
    if (!newTelecallerId || !reason) return
    reassign.mutate(
      { checkout_id: checkoutId, new_telecaller_id: newTelecallerId, reason },
      {
        onSuccess: () => {
          toast.success("Checkout reassigned.")
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
        title="Abandoned Checkout"
        backHref="/team/checkouts"
        backLabel="Back to Abandoned Checkouts"
      />
      <QueryStates
        isLoading={checkoutQuery.isLoading}
        isError={checkoutQuery.isError}
        error={checkoutQuery.error}
        data={checkoutQuery.data}
        onRetry={() => void checkoutQuery.refetch()}
      >
        {(checkout) => (
          <div className="flex flex-col gap-4">
            <div className="grid gap-4 lg:grid-cols-2">
              <Card>
                <CardHeader>
                  <CardTitle>Checkout</CardTitle>
                </CardHeader>
                <CardContent className="grid grid-cols-2 gap-4">
                  <SummaryStat label="Customer" value={checkout.customer_name ?? "—"} />
                  <SummaryStat label="Phone" value={checkout.customer_phone ?? "—"} />
                  <SummaryStat label="Email" value={checkout.customer_email ?? "—"} />
                  <SummaryStat label="Product" value={checkout.item_summary ?? "—"} />
                  <SummaryStat
                    label="Cart Amount"
                    value={formatMoney(checkout.total_amount)}
                  />
                  <SummaryStat
                    label="Checkout Date"
                    value={
                      checkout.checkout_created_at
                        ? formatDateTime(checkout.checkout_created_at)
                        : "—"
                    }
                  />
                  <SummaryStat
                    label="Status"
                    value={checkout.is_recovered ? "Recovered (purchased)" : "Abandoned"}
                  />
                  {checkout.checkout_url && (
                    <SummaryStat
                      label="Recovery Link"
                      value={
                        <a
                          href={checkout.checkout_url}
                          target="_blank"
                          rel="noreferrer"
                          className="text-primary hover:underline"
                        >
                          Open checkout
                        </a>
                      }
                    />
                  )}
                </CardContent>
              </Card>

              <Card>
                <CardHeader className="flex flex-row items-center justify-between gap-2">
                  <CardTitle>Assignment</CardTitle>
                  {checkout.assigned_to && (
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
                    value={checkout.assigned_to_name ?? "Unassigned"}
                  />
                  <SummaryStat
                    label="Call Status"
                    value={
                      <StatusBadge
                        domain="telecalling"
                        status={checkout.call_status ?? "not_called"}
                      />
                    }
                  />
                  <SummaryStat label="Attempts" value={checkout.attempt_count} />
                  <SummaryStat
                    label="Next Follow-up"
                    value={
                      checkout.next_follow_up_at
                        ? formatDateTime(checkout.next_follow_up_at)
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
            <DialogTitle>Reassign Checkout</DialogTitle>
            <DialogDescription>
              Move this checkout to a different telecaller. The previous assignment is
              kept in history, never deleted.
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

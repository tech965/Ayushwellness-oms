"use client"

import * as React from "react"
import Link from "next/link"
import { useParams } from "next/navigation"
import { ExternalLink } from "lucide-react"
import { toast } from "sonner"

import { PaymentStatusFlow } from "@/components/payments/payment-status-flow"
import { PageHeader } from "@/components/shared/page-header"
import { QueryStates } from "@/components/shared/query-states"
import { StatusBadge } from "@/components/shared/status-badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { getApiErrorMessage } from "@/lib/api-client"
import { useAuth } from "@/lib/auth-context"
import { formatDateTime, formatMoney } from "@/lib/format"
import { useWebhookEvents } from "@/services/integrations"
import { usePayment } from "@/services/payments"
import { useReconcileCashfreePayment } from "@/services/cashfree"

export default function PaymentDetailPage() {
  const params = useParams<{ id: string }>()
  const paymentId = params.id
  const { hasPermission } = useAuth()

  const paymentQuery = usePayment(paymentId)
  const payment = paymentQuery.data
  const reconcile = useReconcileCashfreePayment(payment?.order_id ?? "")

  // Only the Cashfree webhook deliveries for THIS exact gateway order --
  // not the whole integration's event log (see the payments list page /
  // `app.api.v1.endpoints.webhook_events`'s new `external_resource_id`
  // filter).
  const webhookEventsQuery = useWebhookEvents({
    externalResourceId: payment?.external_id ?? undefined,
    page: 1,
    pageSize: 20,
  })

  function handleReconcile() {
    reconcile.mutate(undefined, {
      onSuccess: () => toast.success("Reconciled against Cashfree."),
      onError: (error) => toast.error(getApiErrorMessage(error)),
    })
  }

  return (
    <>
      <PageHeader
        title={payment ? `Payment ${payment.id.slice(0, 8)}` : "Payment"}
        description={payment?.external_id ? `Cashfree order: ${payment.external_id}` : undefined}
        backHref="/payments"
        backLabel="Back to Payments"
        actions={
          payment &&
          payment.provider === "cashfree" &&
          payment.status !== "paid" &&
          hasPermission("payments.create") && (
            <Button variant="outline" size="sm" disabled={reconcile.isPending} onClick={handleReconcile}>
              {reconcile.isPending ? "Reconciling..." : "Reconcile with Cashfree"}
            </Button>
          )
        }
      />

      <QueryStates
        isLoading={paymentQuery.isLoading}
        isError={paymentQuery.isError}
        error={paymentQuery.error}
        data={payment}
        onRetry={() => void paymentQuery.refetch()}
      >
        {(payment) => (
          <div className="flex flex-col gap-4">
            <Card>
              <CardHeader className="flex flex-row items-center justify-between gap-2">
                <CardTitle>Payment Overview</CardTitle>
                <StatusBadge domain="payment" status={payment.status} />
              </CardHeader>
              <CardContent className="flex flex-col gap-6">
                <PaymentStatusFlow payment={payment} />
                <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
                  <Stat label="Amount" value={formatMoney(payment.amount, payment.currency)} />
                  <Stat label="Provider" value={payment.provider ?? "—"} />
                  <Stat label="Payment Method" value={payment.payment_method ?? "—"} />
                  <Stat
                    label="Payment Type"
                    value={payment.payment_type.toUpperCase()}
                  />
                  <Stat label="Cashfree Order ID" value={payment.external_id ?? "—"} mono />
                  <Stat
                    label="Payment Session ID"
                    value={payment.payment_session_id ?? "—"}
                    mono
                  />
                  <Stat
                    label="Latest Gateway Transaction ID"
                    value={payment.external_transaction_id ?? "—"}
                    mono
                  />
                  <Stat label="Created At" value={formatDateTime(payment.created_at)} />
                  <Stat label="Updated At" value={formatDateTime(payment.updated_at)} />
                  <Stat
                    label="Paid At"
                    value={payment.paid_at ? formatDateTime(payment.paid_at) : "—"}
                  />
                </div>
              </CardContent>
            </Card>

            <div className="grid gap-4 lg:grid-cols-2">
              <Card>
                <CardHeader className="flex flex-row items-center justify-between gap-2">
                  <CardTitle>Order &amp; Customer</CardTitle>
                  <Link
                    href={`/orders/${payment.order_id}`}
                    className="text-primary hover:text-primary/80 flex items-center gap-1 text-sm font-medium"
                  >
                    View Order
                    <ExternalLink className="size-3.5" />
                  </Link>
                </CardHeader>
                <CardContent className="grid grid-cols-2 gap-4">
                  <Stat label="Order Number" value={payment.order_number ?? "—"} />
                  <Stat label="Customer" value={payment.customer_name ?? "—"} />
                  <Stat label="Phone" value={payment.customer_phone ?? "—"} />
                  <Stat label="Email" value={payment.customer_email ?? "—"} />
                </CardContent>
              </Card>

              <Card>
                <CardHeader>
                  <CardTitle>Failure Reason</CardTitle>
                </CardHeader>
                <CardContent>
                  {(() => {
                    const failureReason = [...payment.transactions]
                      .reverse()
                      .find((t) => t.error_reason)?.error_reason
                    return failureReason ? (
                      <p className="text-danger text-sm">{failureReason}</p>
                    ) : (
                      <p className="text-muted-foreground text-sm">
                        No failure recorded for this payment.
                      </p>
                    )
                  })()}
                </CardContent>
              </Card>
            </div>

            <Card>
              <CardHeader>
                <CardTitle>Transaction History</CardTitle>
              </CardHeader>
              <CardContent>
                {payment.transactions.length === 0 ? (
                  <p className="text-muted-foreground text-sm">
                    No gateway transactions recorded yet.
                  </p>
                ) : (
                  <div className="overflow-x-auto rounded-lg border">
                    <Table>
                      <TableHeader className="bg-muted/40">
                        <TableRow className="hover:bg-transparent">
                          <TableHead>Status</TableHead>
                          <TableHead>Event</TableHead>
                          <TableHead>Method</TableHead>
                          <TableHead className="text-right">Amount</TableHead>
                          <TableHead>Gateway Transaction ID</TableHead>
                          <TableHead>Recorded At</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {payment.transactions.map((txn) => (
                          <TableRow key={txn.id}>
                            <TableCell>
                              <StatusBadge domain="payment" status={txn.status} />
                            </TableCell>
                            <TableCell className="text-xs">{txn.event_type ?? "—"}</TableCell>
                            <TableCell className="text-xs">{txn.payment_method ?? "—"}</TableCell>
                            <TableCell className="text-right">
                              {formatMoney(txn.amount, payment.currency)}
                            </TableCell>
                            <TableCell className="font-mono text-xs">
                              {txn.gateway_transaction_id ?? "—"}
                            </TableCell>
                            <TableCell className="text-xs">
                              {formatDateTime(txn.created_at)}
                            </TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  </div>
                )}
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>Webhook Events</CardTitle>
              </CardHeader>
              <CardContent>
                {!payment.external_id ? (
                  <p className="text-muted-foreground text-sm">
                    No Cashfree checkout has been created for this payment yet.
                  </p>
                ) : webhookEventsQuery.isLoading ? (
                  <p className="text-muted-foreground text-sm">Loading...</p>
                ) : (webhookEventsQuery.data?.data.length ?? 0) === 0 ? (
                  <p className="text-muted-foreground text-sm">
                    No webhook deliveries recorded for this Cashfree order yet.
                  </p>
                ) : (
                  <div className="overflow-x-auto rounded-lg border">
                    <Table>
                      <TableHeader className="bg-muted/40">
                        <TableRow className="hover:bg-transparent">
                          <TableHead>Event Type</TableHead>
                          <TableHead>Status</TableHead>
                          <TableHead>Received</TableHead>
                          <TableHead>Processed</TableHead>
                          <TableHead>Error</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {webhookEventsQuery.data?.data.map((event) => (
                          <TableRow key={event.id}>
                            <TableCell className="text-xs">{event.event_type}</TableCell>
                            <TableCell>
                              <StatusBadge domain="webhook_event" status={event.status} />
                            </TableCell>
                            <TableCell className="text-xs">
                              {formatDateTime(event.received_at)}
                            </TableCell>
                            <TableCell className="text-xs">
                              {event.processed_at ? formatDateTime(event.processed_at) : "—"}
                            </TableCell>
                            <TableCell className="text-xs">
                              {event.error_message ?? "—"}
                            </TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  </div>
                )}
              </CardContent>
            </Card>
          </div>
        )}
      </QueryStates>
    </>
  )
}

function Stat({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div>
      <p className="text-muted-foreground text-xs">{label}</p>
      <p className={mono ? "font-mono text-sm break-all" : "text-sm font-medium break-words"}>
        {value}
      </p>
    </div>
  )
}

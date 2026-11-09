"use client"

import * as React from "react"

import { LiveResultNotice } from "@/components/shared/live-result-notice"
import { PageHeader } from "@/components/shared/page-header"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { getApiErrorMessage } from "@/lib/api-client"
import { formatMoney } from "@/lib/format"
import {
  useLivePaymentSummary,
  useLiveRefunds,
  useLiveSettlements,
  useLiveTransactions,
  type LiveBoundedResult,
  type LiveTransaction,
} from "@/services/live"

function money(value: string | null): string {
  return value === null ? "—" : formatMoney(value)
}

function when(value: string | null): string {
  return value ? new Date(value).toLocaleString("en-IN") : "—"
}

function Bounded({ result }: { result: LiveBoundedResult | undefined }) {
  return (
    <LiveResultNotice
      meta={result && !result.complete ? { warnings: result.warnings } : undefined}
    />
  )
}

function Loading({ error, loading }: { error: unknown; loading: boolean }) {
  if (error) {
    return (
      <Alert variant="destructive">
        <AlertTitle>Could not load from Cashfree</AlertTitle>
        <AlertDescription>{getApiErrorMessage(error)}</AlertDescription>
      </Alert>
    )
  }
  return loading ? <Skeleton className="h-32 w-full" /> : null
}

function TransactionTable({ rows, empty }: { rows: LiveTransaction[]; empty: string }) {
  if (rows.length === 0) return <p className="text-sm text-muted-foreground">{empty}</p>
  return (
    <table className="w-full text-sm" data-testid="live-transactions">
      <thead>
        <tr className="text-left text-muted-foreground">
          <th className="py-2">Order</th>
          <th>Payment</th>
          <th>Status</th>
          <th className="text-right">Amount</th>
          <th>Time</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row, index) => (
          <tr key={`${row.cf_payment_id}-${row.event_type}-${index}`} className="border-t">
            <td className="py-2">{row.cashfree_order_id ?? "—"}</td>
            <td>{row.cf_payment_id ?? "—"}</td>
            <td>{row.status}</td>
            <td className="text-right tabular-nums">{money(row.amount)}</td>
            <td>{when(row.time)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

/** Payments screen while Cashfree is read live: nothing here is stored
 * locally. Each tab is a bounded live read and says so when it is partial. */
export function LivePayments() {
  const range: { date_from?: string; date_to?: string } = React.useMemo(() => ({}), [])
  const summary = useLivePaymentSummary(range)
  const transactions = useLiveTransactions(range)
  const refunds = useLiveRefunds(range)
  const settlements = useLiveSettlements(range)

  return (
    <>
      <PageHeader
        title="Payments"
        description="Read live from Cashfree (last 7 days by default). Nothing on this page is stored."
      />
      <Tabs defaultValue="summary" className="flex flex-col gap-4" data-testid="live-payments">
        <TabsList>
          <TabsTrigger value="summary">Summary</TabsTrigger>
          <TabsTrigger value="transactions">Transactions</TabsTrigger>
          <TabsTrigger value="refunds">Refunds</TabsTrigger>
          <TabsTrigger value="settlements">Settlements</TabsTrigger>
        </TabsList>

        <TabsContent value="summary" className="flex flex-col gap-4">
          <Loading error={summary.error} loading={summary.isLoading} />
          {summary.data ? (
            <>
              <Bounded result={summary.data} />
              <div className="grid gap-4 sm:grid-cols-2">
                <Card>
                  <CardHeader>
                    <CardTitle className="text-sm font-medium">Payments received</CardTitle>
                  </CardHeader>
                  <CardContent className="text-2xl font-semibold tabular-nums">
                    {money(summary.data.payments_received)}
                  </CardContent>
                </Card>
                <Card>
                  <CardHeader>
                    <CardTitle className="text-sm font-medium">Refunded</CardTitle>
                  </CardHeader>
                  <CardContent className="text-2xl font-semibold tabular-nums">
                    {money(summary.data.refunded)}
                  </CardContent>
                </Card>
              </div>
              <Card>
                <CardHeader>
                  <CardTitle className="text-sm font-medium">By status</CardTitle>
                </CardHeader>
                <CardContent>
                  {summary.data.by_status.length === 0 ? (
                    <p className="text-sm text-muted-foreground">No payments in this range.</p>
                  ) : (
                    <ul className="flex flex-col gap-2 text-sm">
                      {summary.data.by_status.map((row) => (
                        <li key={row.status} className="flex justify-between">
                          <span className="capitalize">{row.status}</span>
                          <span className="tabular-nums">
                            {row.count} · {money(row.amount)}
                          </span>
                        </li>
                      ))}
                    </ul>
                  )}
                </CardContent>
              </Card>
            </>
          ) : null}
        </TabsContent>

        <TabsContent value="transactions" className="flex flex-col gap-4">
          <Loading error={transactions.error} loading={transactions.isLoading} />
          {transactions.data ? (
            <>
              <Bounded result={transactions.data} />
              <TransactionTable
                rows={transactions.data.items}
                empty="No transactions in this range."
              />
            </>
          ) : null}
        </TabsContent>

        <TabsContent value="refunds" className="flex flex-col gap-4">
          <Loading error={refunds.error} loading={refunds.isLoading} />
          {refunds.data ? (
            <>
              <Bounded result={refunds.data} />
              <TransactionTable rows={refunds.data.items} empty="No refunds in this range." />
            </>
          ) : null}
        </TabsContent>

        <TabsContent value="settlements" className="flex flex-col gap-4">
          <Loading error={settlements.error} loading={settlements.isLoading} />
          {settlements.data ? (
            <>
              <Bounded result={settlements.data} />
              <p className="text-sm text-muted-foreground" data-testid="live-unsettled">
                Unsettled: {money(settlements.data.unsettled_amount)}
                {settlements.data.unsettled_amount_is_derived
                  ? " (derived from settlement statuses, not a Cashfree-reported figure)"
                  : ""}
              </p>
              {settlements.data.items.length === 0 ? (
                <p className="text-sm text-muted-foreground">No settlements in this range.</p>
              ) : (
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-left text-muted-foreground">
                      <th className="py-2">Settlement</th>
                      <th>Status</th>
                      <th>UTR</th>
                      <th className="text-right">Amount</th>
                      <th className="text-right">Settled</th>
                    </tr>
                  </thead>
                  <tbody>
                    {settlements.data.items.map((row) => (
                      <tr key={row.cf_settlement_id ?? row.utr ?? "row"} className="border-t">
                        <td className="py-2">{row.cf_settlement_id ?? "—"}</td>
                        <td>{row.status ?? "—"}</td>
                        <td>{row.utr ?? "—"}</td>
                        <td className="text-right tabular-nums">{money(row.payment_amount)}</td>
                        <td className="text-right tabular-nums">{money(row.amount_settled)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </>
          ) : null}
        </TabsContent>
      </Tabs>
    </>
  )
}

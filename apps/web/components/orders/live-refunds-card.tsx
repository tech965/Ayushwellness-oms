"use client"

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { getApiErrorMessage } from "@/lib/api-client"
import { formatDateTime, formatMoney } from "@/lib/format"
import { useLiveOrderRefunds } from "@/services/live"

/** Refunds recorded on the Shopify order, read live. Also shows the
 * order's cancellation state, because Shopify is the authority for it. */
export function LiveRefundsCard({ orderId }: { orderId: string }) {
  const query = useLiveOrderRefunds(orderId)
  return (
    <Card data-testid="live-refunds-card">
      <CardHeader>
        <CardTitle>Refunds (live from Shopify)</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-2 text-sm">
        {query.isLoading ? <Skeleton className="h-12 w-full" /> : null}
        {query.isError ? (
          <Alert variant="destructive">
            <AlertTitle>Could not load refunds from Shopify</AlertTitle>
            <AlertDescription>{getApiErrorMessage(query.error)}</AlertDescription>
          </Alert>
        ) : null}
        {query.data ? (
          <>
            {query.data.cancelled_at ? (
              <p data-testid="live-cancelled">
                Cancelled in Shopify on {formatDateTime(query.data.cancelled_at)}
                {query.data.cancel_reason ? ` (${query.data.cancel_reason.toLowerCase()})` : ""}.
              </p>
            ) : null}
            {query.data.refunds.length === 0 ? (
              <p className="text-muted-foreground">No refunds.</p>
            ) : (
              query.data.refunds.map((refund) => (
                <div key={refund.id} className="flex items-center justify-between">
                  <span>
                    {refund.amount ? formatMoney(refund.amount, query.data.currency ?? "INR") : "—"}
                    {refund.note ? ` · ${refund.note}` : ""}
                  </span>
                  <span className="text-muted-foreground text-xs">
                    {refund.processed_at ? formatDateTime(refund.processed_at) : "—"}
                  </span>
                </div>
              ))
            )}
          </>
        ) : null}
      </CardContent>
    </Card>
  )
}

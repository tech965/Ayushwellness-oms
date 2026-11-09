"use client"

import * as React from "react"
import { toast } from "sonner"

import { loadCashfreeSdk } from "@/components/orders/cashfree-payment-card"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { getApiErrorMessage } from "@/lib/api-client"
import { useAuth } from "@/lib/auth-context"
import { formatDateTime, formatMoney } from "@/lib/format"
import { useLiveCreateCheckout, useLiveOrderPayment } from "@/services/live"

const OUTCOME_TEXT: Record<string, string> = {
  match: "Cashfree and Shopify agree on this order's payment status.",
  mismatch: "Cashfree and Shopify disagree on this order's payment status. Neither was changed.",
  not_found_in_cashfree: "Cashfree has no record of this order.",
}

interface LivePaymentCardProps {
  orderId: string
  orderPaymentStatus: string
}

/** Order-detail payment panel while Cashfree is read live. The figures are
 * Cashfree's current answer (verified), compared read-only with the status
 * Shopify reports; nothing is stored or corrected. */
export function LivePaymentCard({ orderId, orderPaymentStatus }: LivePaymentCardProps) {
  const { hasPermission } = useAuth()
  const query = useLiveOrderPayment(orderId)
  const checkout = useLiveCreateCheckout(orderId)

  async function openCheckout() {
    try {
      const result = await checkout.mutateAsync()
      if (!result?.payment_session_id) {
        toast.error("Cashfree did not return a payment session.")
        return
      }
      await loadCashfreeSdk()
      window.Cashfree?.({ mode: result.mode === "production" ? "production" : "sandbox" }).checkout({
        paymentSessionId: result.payment_session_id,
        redirectTarget: "_blank",
      })
      void query.refetch()
    } catch (error) {
      toast.error(getApiErrorMessage(error))
    }
  }

  return (
    <Card data-testid="live-payment-card">
      <CardHeader>
        <CardTitle className="text-sm font-medium">Payment (live from Cashfree)</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-3 text-sm">
        {query.isLoading ? <Skeleton className="h-16 w-full" /> : null}
        {query.isError ? (
          <Alert variant="destructive">
            <AlertTitle>Could not verify with Cashfree</AlertTitle>
            <AlertDescription>
              {getApiErrorMessage(query.error)} Shopify reports this order as {orderPaymentStatus}.
            </AlertDescription>
          </Alert>
        ) : null}
        {query.data ? (
          <>
            <p data-testid="live-payment-outcome">
              {OUTCOME_TEXT[query.data.outcome] ?? query.data.outcome}
            </p>
            <p className="text-muted-foreground">
              Verified by Cashfree: {query.data.found ? (query.data.paid ? "paid" : "not paid") : "—"}
              {" · "}Shopify says: {query.data.shopify_payment_status}
            </p>
            {query.data.payments.length === 0 ? (
              <p className="text-muted-foreground">No payment attempts found.</p>
            ) : (
              <ul className="flex flex-col gap-1">
                {query.data.payments.map((payment, index) => (
                  <li key={`${payment.cf_payment_id}-${index}`} className="flex justify-between">
                    <span className="capitalize">
                      {payment.status}
                      {payment.method ? ` · ${payment.method}` : ""}
                    </span>
                    <span className="tabular-nums">
                      {payment.amount ? formatMoney(payment.amount) : "—"}
                      {payment.paid_at ? ` · ${formatDateTime(payment.paid_at)}` : ""}
                    </span>
                  </li>
                ))}
              </ul>
            )}
            {hasPermission("payments.create") && orderPaymentStatus !== "paid" && !query.data.paid ? (
              <Button size="sm" disabled={checkout.isPending} onClick={() => void openCheckout()}>
                Collect payment via Cashfree
              </Button>
            ) : null}
          </>
        ) : null}
      </CardContent>
    </Card>
  )
}

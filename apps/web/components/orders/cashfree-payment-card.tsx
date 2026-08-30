"use client"

import * as React from "react"
import axios from "axios"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { StatusBadge } from "@/components/shared/status-badge"
import { getApiErrorMessage } from "@/lib/api-client"
import { useAuth } from "@/lib/auth-context"
import { formatDateTime, formatMoney } from "@/lib/format"
import {
  useCashfreePayment,
  useCreateCashfreeCheckout,
  useReconcileCashfreePayment,
} from "@/services/cashfree"

// Cashfree's official Checkout JS SDK -- loaded once, lazily, only when
// a checkout is actually opened (never on page load, and never for a
// user without `payments.create`). No client/webhook secret is ever
// present in this file or sent to the browser -- only the
// `payment_session_id` the backend already computed server-side.
const CASHFREE_SDK_URL = "https://sdk.cashfree.com/js/v3/cashfree.js"

declare global {
  interface Window {
    Cashfree?: (options: { mode: "sandbox" | "production" }) => {
      checkout: (options: { paymentSessionId: string; redirectTarget?: string }) => void
    }
  }
}

let cashfreeSdkPromise: Promise<void> | null = null

function loadCashfreeSdk(): Promise<void> {
  if (typeof window === "undefined") return Promise.resolve()
  if (window.Cashfree) return Promise.resolve()
  cashfreeSdkPromise ??= new Promise((resolve, reject) => {
    const script = document.createElement("script")
    script.src = CASHFREE_SDK_URL
    script.async = true
    script.onload = () => resolve()
    script.onerror = () => reject(new Error("Failed to load Cashfree checkout SDK."))
    document.body.appendChild(script)
  })
  return cashfreeSdkPromise
}

interface CashfreePaymentCardProps {
  orderId: string
  orderPaymentStatus: string
}

export function CashfreePaymentCard({ orderId, orderPaymentStatus }: CashfreePaymentCardProps) {
  const { hasPermission } = useAuth()
  const paymentQuery = useCashfreePayment(orderId)
  const createCheckout = useCreateCashfreeCheckout(orderId)
  const reconcile = useReconcileCashfreePayment(orderId)
  const [opening, setOpening] = React.useState(false)

  const canCreate = hasPermission("payments.create")
  const alreadyPaid = orderPaymentStatus === "paid"
  const payment = paymentQuery.data
  const is404 = !paymentQuery.isLoading && payment === null

  async function openCheckout(sessionId: string, mode: "sandbox" | "production") {
    setOpening(true)
    try {
      await loadCashfreeSdk()
      const cashfree = window.Cashfree?.({ mode })
      if (!cashfree) throw new Error("Cashfree checkout SDK did not initialize.")
      cashfree.checkout({ paymentSessionId: sessionId, redirectTarget: "_modal" })
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Could not open Cashfree checkout.")
    } finally {
      setOpening(false)
    }
  }

  function handleCreate() {
    createCheckout.mutate(undefined, {
      onSuccess: (data) => {
        if (data?.payment_session_id) {
          toast.success(data.created ? "Cashfree checkout created." : "Resuming existing session.")
          void openCheckout(data.payment_session_id, data.mode)
        }
      },
      onError: (error) => toast.error(getApiErrorMessage(error)),
    })
  }

  function handleReconcile() {
    reconcile.mutate(undefined, {
      onSuccess: () => toast.success("Reconciled against Cashfree."),
      onError: (error) => toast.error(getApiErrorMessage(error)),
    })
  }

  const isPaymentError =
    paymentQuery.isError &&
    !(axios.isAxiosError(paymentQuery.error) && paymentQuery.error.response?.status === 404)

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between gap-2">
        <CardTitle>Cashfree Payment</CardTitle>
        {payment && <StatusBadge domain="payment" status={payment.status} />}
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        {paymentQuery.isLoading && (
          <p className="text-muted-foreground text-sm">Loading...</p>
        )}

        {isPaymentError && (
          <p className="text-destructive text-sm">{getApiErrorMessage(paymentQuery.error)}</p>
        )}

        {payment && (
          <div className="flex flex-col gap-1 text-sm">
            <div className="flex items-center justify-between">
              <span className="font-medium">{formatMoney(payment.amount, payment.currency)}</span>
              {payment.payment_method && (
                <span className="text-muted-foreground uppercase">{payment.payment_method}</span>
              )}
            </div>
            <div className="text-muted-foreground flex flex-wrap gap-x-3 text-xs">
              <span className="font-mono">{payment.cashfree_order_id}</span>
              {payment.paid_at && <span>Paid {formatDateTime(payment.paid_at)}</span>}
            </div>
          </div>
        )}

        {is404 && (
          <p className="text-muted-foreground text-sm">
            No Cashfree payment has been initiated for this order yet.
          </p>
        )}

        {canCreate && !alreadyPaid && (
          <div className="flex flex-wrap gap-2">
            {(is404 || (payment && payment.status !== "paid")) && (
              <Button
                size="sm"
                disabled={createCheckout.isPending || opening}
                onClick={handleCreate}
              >
                {createCheckout.isPending || opening
                  ? "Opening..."
                  : payment
                    ? "Resume Checkout"
                    : "Collect Payment via Cashfree"}
              </Button>
            )}
            {payment && payment.status !== "paid" && (
              <Button
                size="sm"
                variant="outline"
                disabled={reconcile.isPending}
                onClick={handleReconcile}
              >
                {reconcile.isPending ? "Checking..." : "Check Cashfree Status"}
              </Button>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  )
}

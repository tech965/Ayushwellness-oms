"use client"

import Link from "next/link"

import { LivePaymentCard } from "@/components/orders/live-payment-card"
import { PageHeader } from "@/components/shared/page-header"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Skeleton } from "@/components/ui/skeleton"
import { getApiErrorMessage } from "@/lib/api-client"
import { useOrder } from "@/services/orders"

/** Payment detail while Cashfree is read live. No payment record is stored,
 * so there is no payment id: the route key is the OMS ORDER id, and the
 * figures are Cashfree's current answer for that order. */
export function LivePaymentDetail({ orderId }: { orderId: string }) {
  const order = useOrder(orderId)
  return (
    <>
      <PageHeader
        title={order.data ? `Payment for ${order.data.order_number}` : "Payment"}
        description="Read live from Cashfree. Verified figures come from Cashfree; the comparison uses Shopify's reported status."
        backHref="/payments"
        backLabel="Back to Payments"
      />
      {order.isLoading ? <Skeleton className="h-24 w-full" /> : null}
      {order.isError ? (
        <Alert variant="destructive">
          <AlertTitle>Order not found</AlertTitle>
          <AlertDescription>{getApiErrorMessage(order.error)}</AlertDescription>
        </Alert>
      ) : null}
      {order.data ? (
        <div className="flex flex-col gap-4" data-testid="live-payment-detail">
          <LivePaymentCard orderId={orderId} orderPaymentStatus={order.data.payment_status} />
          <Link className="text-sm underline" href={`/orders/${orderId}`}>
            Open order {order.data.order_number}
          </Link>
        </div>
      ) : null}
    </>
  )
}

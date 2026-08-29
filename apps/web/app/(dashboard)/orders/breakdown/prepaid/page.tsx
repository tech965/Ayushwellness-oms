"use client"

import * as React from "react"

import { OrdersPaymentDrilldownContent } from "@/components/analytics/orders-payment-drilldown-content"
import { PageHeader } from "@/components/shared/page-header"
import { Skeleton } from "@/components/ui/skeleton"

function PrepaidOrdersSkeleton() {
  return (
    <>
      <PageHeader
        title="Prepaid Orders"
        backHref="/orders/breakdown"
        backLabel="Back to Order Breakdown"
      />
      <Skeleton className="h-64 w-full" />
    </>
  )
}

export default function PrepaidOrdersPage() {
  return (
    <React.Suspense fallback={<PrepaidOrdersSkeleton />}>
      <OrdersPaymentDrilldownContent
        paymentType="prepaid"
        label="Prepaid"
        accent="blue"
        seriesColor="var(--info)"
      />
    </React.Suspense>
  )
}

"use client"

import * as React from "react"

import { OrdersPaymentDrilldownContent } from "@/components/analytics/orders-payment-drilldown-content"
import { PageHeader } from "@/components/shared/page-header"
import { Skeleton } from "@/components/ui/skeleton"

function CodOrdersSkeleton() {
  return (
    <>
      <PageHeader title="COD Orders" backHref="/orders/breakdown" backLabel="Back to Order Breakdown" />
      <Skeleton className="h-64 w-full" />
    </>
  )
}

export default function CodOrdersPage() {
  return (
    <React.Suspense fallback={<CodOrdersSkeleton />}>
      <OrdersPaymentDrilldownContent
        paymentType="cod"
        label="COD"
        accent="amber"
        seriesColor="var(--warning)"
      />
    </React.Suspense>
  )
}

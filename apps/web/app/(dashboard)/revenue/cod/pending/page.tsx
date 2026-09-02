"use client"

import * as React from "react"

import { CodPendingFulfillmentContent } from "@/components/analytics/cod-pending-fulfillment-content"
import { PageHeader } from "@/components/shared/page-header"
import { Skeleton } from "@/components/ui/skeleton"

function CodPendingFulfillmentSkeleton() {
  return (
    <>
      <PageHeader
        title="Pending COD — Fulfillment Status"
        backHref="/revenue/cod"
        backLabel="Back to COD Revenue"
      />
      <Skeleton className="h-64 w-full" />
    </>
  )
}

export default function CodPendingFulfillmentPage() {
  return (
    <React.Suspense fallback={<CodPendingFulfillmentSkeleton />}>
      <CodPendingFulfillmentContent />
    </React.Suspense>
  )
}

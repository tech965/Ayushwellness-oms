"use client"

import * as React from "react"

import { RevenueDrilldownContent } from "@/components/analytics/revenue-drilldown-content"
import { PageHeader } from "@/components/shared/page-header"
import { Skeleton } from "@/components/ui/skeleton"

function CodRevenueSkeleton() {
  return (
    <>
      <PageHeader title="COD Revenue" backHref="/revenue" backLabel="Back to Revenue Analytics" />
      <Skeleton className="h-64 w-full" />
    </>
  )
}

export default function CodRevenuePage() {
  return (
    <React.Suspense fallback={<CodRevenueSkeleton />}>
      <RevenueDrilldownContent
        paymentType="cod"
        label="COD"
        accent="amber"
        seriesColor="var(--warning)"
      />
    </React.Suspense>
  )
}

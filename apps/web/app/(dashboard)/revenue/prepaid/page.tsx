"use client"

import * as React from "react"

import { RevenueDrilldownContent } from "@/components/analytics/revenue-drilldown-content"
import { PageHeader } from "@/components/shared/page-header"
import { Skeleton } from "@/components/ui/skeleton"

function PrepaidRevenueSkeleton() {
  return (
    <>
      <PageHeader
        title="Prepaid Revenue"
        backHref="/revenue"
        backLabel="Back to Revenue Analytics"
      />
      <Skeleton className="h-64 w-full" />
    </>
  )
}

export default function PrepaidRevenuePage() {
  return (
    <React.Suspense fallback={<PrepaidRevenueSkeleton />}>
      <RevenueDrilldownContent
        paymentType="prepaid"
        label="Prepaid"
        accent="blue"
        seriesColor="var(--info)"
      />
    </React.Suspense>
  )
}

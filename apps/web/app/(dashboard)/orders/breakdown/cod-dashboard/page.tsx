"use client"

import * as React from "react"

import { CodDashboardContent, CodDashboardSkeleton } from "@/components/analytics/cod-dashboard-content"

export default function CodDashboardPage() {
  return (
    <React.Suspense fallback={<CodDashboardSkeleton />}>
      <CodDashboardContent />
    </React.Suspense>
  )
}

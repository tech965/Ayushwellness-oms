"use client"

import * as React from "react"

import { RemittanceDashboard } from "@/components/remittance/remittance-dashboard"

/** Module 6 -- COD Remittance Reconciliation (under the existing
 * "Reconciliation" sidebar entry). `useDateRangeFilter` reads the URL, which
 * Next.js requires to sit under a Suspense boundary.
 */
export default function CodRemittancePage() {
  return (
    <React.Suspense
      fallback={<div className="text-muted-foreground p-4 text-sm">Loading…</div>}
    >
      <RemittanceDashboard module="cod" />
    </React.Suspense>
  )
}

"use client"

import * as React from "react"

import { RemittanceDashboard } from "@/components/remittance/remittance-dashboard"

/** Module 7 -- Prepaid Remittance Reconciliation (under the existing
 * "Reconciliation" sidebar entry).
 */
export default function PrepaidRemittancePage() {
  return (
    <React.Suspense
      fallback={<div className="text-muted-foreground p-4 text-sm">Loading…</div>}
    >
      <RemittanceDashboard module="prepaid" />
    </React.Suspense>
  )
}

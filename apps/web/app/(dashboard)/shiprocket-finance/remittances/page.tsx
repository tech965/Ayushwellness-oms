"use client"

import * as React from "react"

import { FinanceDashboard } from "@/components/shiprocket-finance/finance-dashboard"

/** Shiprocket Remittances. `useDateRangeFilter` reads the URL, which Next.js
 * requires to sit under a Suspense boundary. */
export default function ShiprocketRemittancesPage() {
  return (
    <React.Suspense
      fallback={<div className="text-muted-foreground p-4 text-sm">Loading…</div>}
    >
      <FinanceDashboard kind="remittances" />
    </React.Suspense>
  )
}

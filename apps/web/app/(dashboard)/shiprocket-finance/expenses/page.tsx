"use client"

import * as React from "react"

import { FinanceDashboard } from "@/components/shiprocket-finance/finance-dashboard"

/** Shiprocket Expenses. `useDateRangeFilter` reads the URL, which Next.js
 * requires to sit under a Suspense boundary. */
export default function ShiprocketExpensesPage() {
  return (
    <React.Suspense
      fallback={<div className="text-muted-foreground p-4 text-sm">Loading…</div>}
    >
      <FinanceDashboard kind="expenses" />
    </React.Suspense>
  )
}

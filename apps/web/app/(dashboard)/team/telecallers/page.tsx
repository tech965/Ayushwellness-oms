"use client"

import { PageHeader } from "@/components/shared/page-header"
import { TelecallerPerformanceTable } from "@/components/team/telecaller-performance-table"

export default function TeamTelecallersPage() {
  return (
    <>
      <PageHeader
        title="Telecallers"
        description="Your team's telecallers and their calling performance. Click a row for their assigned workload."
      />
      <TelecallerPerformanceTable />
    </>
  )
}

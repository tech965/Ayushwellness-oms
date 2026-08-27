"use client"

import Link from "next/link"
import {
  CalendarClock,
  CheckCircle2,
  PhoneCall,
  PhoneIncoming,
  ThumbsDown,
  Users,
} from "lucide-react"

import { PageHeader } from "@/components/shared/page-header"
import { StatTile } from "@/components/shared/stat-tile"
import { TelecallerPerformanceTable } from "@/components/team/telecaller-performance-table"
import { Skeleton } from "@/components/ui/skeleton"
import { useTeamSummary } from "@/services/team"

export default function TeamDashboardPage() {
  const { data: summary, isLoading: summaryLoading } = useTeamSummary()

  return (
    <>
      <PageHeader
        title="Team Dashboard"
        description="Your team's telecalling activity."
      />

      {summaryLoading ? (
        <div className="grid grid-cols-2 gap-4 lg:grid-cols-3 xl:grid-cols-6">
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-24 w-full" />
          ))}
        </div>
      ) : (
        <div className="grid grid-cols-2 gap-4 lg:grid-cols-3 xl:grid-cols-6">
          <StatTile
            label="Total Assigned"
            value={summary?.assigned ?? 0}
            icon={Users}
            accent="blue"
          />
          <StatTile
            label="Pending Calls"
            value={summary?.pending ?? 0}
            icon={PhoneIncoming}
            accent="amber"
          />
          <StatTile
            label="Today's Follow-ups"
            value={summary?.follow_ups_today ?? 0}
            icon={CalendarClock}
            accent="orange"
          />
          <StatTile
            label="Connected"
            value={summary?.connected ?? 0}
            icon={PhoneCall}
            accent="violet"
          />
          <StatTile
            label="Confirmed"
            value={summary?.confirmed ?? 0}
            icon={CheckCircle2}
            accent="emerald"
          />
          <StatTile
            label="Not Interested"
            value={summary?.not_interested ?? 0}
            icon={ThumbsDown}
            accent="slate"
          />
        </div>
      )}

      <div className="mt-6">
        <TelecallerPerformanceTable />
      </div>

      <div className="mt-6">
        <Link
          href="/team/orders/unfulfilled"
          className="border-border bg-card hover:border-primary/40 inline-block rounded-lg border p-4 text-sm font-medium transition-colors"
        >
          Go to Unfulfilled Orders →
        </Link>
      </div>
    </>
  )
}

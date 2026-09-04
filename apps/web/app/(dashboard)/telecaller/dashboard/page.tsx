"use client"

import Link from "next/link"
import {
  CalendarClock,
  CheckCircle2,
  MousePointerClick,
  PhoneCall,
  PhoneIncoming,
  Users,
} from "lucide-react"

import { StatTile } from "@/components/shared/stat-tile"
import { PageHeader } from "@/components/shared/page-header"
import { Skeleton } from "@/components/ui/skeleton"
import { useMySummary } from "@/services/telecaller"

export default function TelecallerDashboardPage() {
  const { data: summary, isLoading } = useMySummary()

  return (
    <>
      <PageHeader title="My Dashboard" description="Your calling activity at a glance." />
      {isLoading ? (
        <div className="grid grid-cols-2 gap-4 lg:grid-cols-3 xl:grid-cols-7">
          {Array.from({ length: 7 }).map((_, i) => (
            <Skeleton key={i} className="h-24 w-full" />
          ))}
        </div>
      ) : (
        <div className="grid grid-cols-2 gap-4 lg:grid-cols-3 xl:grid-cols-7">
          <StatTile
            label="Assigned"
            value={summary?.assigned ?? 0}
            icon={Users}
            accent="blue"
          />
          <StatTile
            label="Pending"
            value={summary?.pending ?? 0}
            icon={PhoneIncoming}
            accent="amber"
          />
          <StatTile
            label="Called"
            value={summary?.called ?? 0}
            icon={PhoneCall}
            accent="violet"
          />
          <StatTile
            label="Connected"
            value={summary?.connected ?? 0}
            icon={PhoneCall}
            accent="blue"
          />
          <StatTile
            label="Follow-ups Today"
            value={summary?.follow_ups_today ?? 0}
            icon={CalendarClock}
            accent="orange"
          />
          <StatTile
            label="Converted"
            value={summary?.confirmed ?? 0}
            icon={CheckCircle2}
            accent="emerald"
          />
          <StatTile
            label="Checkout Leads"
            value={summary?.abandoned_checkouts ?? 0}
            icon={MousePointerClick}
            accent="violet"
          />
        </div>
      )}

      <div className="mt-6 flex flex-wrap gap-3">
        <Link
          href="/telecaller/orders"
          className="border-border bg-card hover:border-primary/40 min-w-[220px] flex-1 rounded-lg border p-4 text-sm font-medium transition-colors"
        >
          View my assigned orders →
        </Link>
        <Link
          href="/telecaller/checkouts"
          className="border-border bg-card hover:border-primary/40 min-w-[220px] flex-1 rounded-lg border p-4 text-sm font-medium transition-colors"
        >
          View my checkout leads →
        </Link>
        <Link
          href="/telecaller/follow-ups"
          className="border-border bg-card hover:border-primary/40 min-w-[220px] flex-1 rounded-lg border p-4 text-sm font-medium transition-colors"
        >
          Check today&apos;s follow-ups →
        </Link>
      </div>
    </>
  )
}

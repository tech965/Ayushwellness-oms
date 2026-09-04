"use client"

import Link from "next/link"
import {
  CalendarClock,
  CheckCircle2,
  MousePointerClick,
  PackageX,
  PhoneCall,
  PhoneIncoming,
  ShoppingCart,
  ThumbsDown,
  Truck,
  UserPlus,
  Users,
  Wallet,
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
        <div className="grid grid-cols-2 gap-4 lg:grid-cols-4 xl:grid-cols-6">
          {Array.from({ length: 12 }).map((_, i) => (
            <Skeleton key={i} className="h-24 w-full" />
          ))}
        </div>
      ) : (
        <>
          <div className="grid grid-cols-2 gap-4 lg:grid-cols-4 xl:grid-cols-6">
            <StatTile
              label="Total Leads"
              value={summary?.total_leads ?? 0}
              icon={Users}
              accent="blue"
            />
            <StatTile
              label="Unassigned Leads"
              value={summary?.unassigned_leads ?? 0}
              icon={UserPlus}
              accent="slate"
            />
            <StatTile
              label="Pending Calls"
              value={summary?.pending ?? 0}
              icon={PhoneIncoming}
              accent="amber"
            />
            <StatTile
              label="Callbacks Due Today"
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
              label="Converted"
              value={summary?.confirmed ?? 0}
              icon={CheckCircle2}
              accent="emerald"
            />
          </div>

          <div className="mt-4 grid grid-cols-2 gap-4 lg:grid-cols-4 xl:grid-cols-6">
            <StatTile
              label="Abandoned Checkouts"
              value={summary?.abandoned_checkouts ?? 0}
              icon={MousePointerClick}
              accent="violet"
            />
            <StatTile
              label="COD Unfulfilled"
              value={summary?.cod_unfulfilled ?? 0}
              icon={PackageX}
              accent="amber"
            />
            <StatTile
              label="COD Fulfilled"
              value={summary?.cod_fulfilled ?? 0}
              icon={Truck}
              accent="blue"
            />
            <StatTile
              label="Prepaid"
              value={summary?.prepaid ?? 0}
              icon={Wallet}
              accent="emerald"
            />
            <StatTile
              label="Not Interested"
              value={summary?.not_interested ?? 0}
              icon={ThumbsDown}
              accent="slate"
            />
            <StatTile
              label="Completed Calls"
              value={summary ? summary.called : 0}
              icon={ShoppingCart}
              accent="violet"
            />
          </div>
        </>
      )}

      <div className="mt-6">
        <TelecallerPerformanceTable />
      </div>

      <div className="mt-6 flex flex-wrap gap-3">
        <Link
          href="/team/leads"
          className="border-border bg-card hover:border-primary/40 inline-block rounded-lg border p-4 text-sm font-medium transition-colors"
        >
          Go to Lead Pool →
        </Link>
        <Link
          href="/team/checkouts"
          className="border-border bg-card hover:border-primary/40 inline-block rounded-lg border p-4 text-sm font-medium transition-colors"
        >
          Go to Abandoned Checkouts →
        </Link>
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

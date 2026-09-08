"use client"

import * as React from "react"
import { useParams, useRouter } from "next/navigation"
import {
  CalendarClock,
  CheckCircle2,
  ListChecks,
  PhoneCall,
  Target,
  ThumbsDown,
  Truck,
  UserCheck,
  XCircle,
} from "lucide-react"

import { DataTable, type DataTableColumn } from "@/components/shared/data-table"
import { PageHeader } from "@/components/shared/page-header"
import { PaginationBar } from "@/components/shared/pagination-bar"
import { QueryStates } from "@/components/shared/query-states"
import { StatTile } from "@/components/shared/stat-tile"
import { StatusBadge } from "@/components/shared/status-badge"
import { TelecallerDailyPerformanceChart } from "@/components/team/telecaller-daily-performance-chart"
import { formatDate, formatMoney } from "@/lib/format"
import { useUrlFilters } from "@/lib/use-url-filters"
import {
  useTelecallerDailyPerformance,
  useTelecallerOrders,
  useTelecallerSummary,
} from "@/services/team"
import type { AssignedOrder } from "@/types/telecalling"

const FILTER_DEFAULTS = { page: 1, page_size: 20 }

const columns: DataTableColumn<AssignedOrder>[] = [
  {
    id: "order_number",
    header: "Order",
    cell: (o) => <span className="font-medium">{o.order_number}</span>,
  },
  { id: "customer_name", header: "Customer", cell: (o) => o.customer_name ?? "—" },
  { id: "customer_phone", header: "Phone", cell: (o) => o.customer_phone ?? "—" },
  {
    id: "total_amount",
    header: "Amount",
    className: "text-right",
    cell: (o) => formatMoney(o.total_amount),
  },
  {
    id: "call_status",
    header: "Call Status",
    cell: (o) => (
      <StatusBadge domain="telecalling" status={o.call_status ?? "not_called"} />
    ),
  },
  {
    id: "attempt_count",
    header: "Attempts",
    className: "text-right",
    cell: (o) => o.attempt_count,
  },
  {
    id: "next_follow_up_at",
    header: "Next Follow-up",
    cell: (o) => (o.next_follow_up_at ? formatDate(o.next_follow_up_at) : "—"),
  },
]

export default function TeamTelecallerWorkloadPage() {
  return (
    <React.Suspense>
      <TeamTelecallerWorkloadContent />
    </React.Suspense>
  )
}

function TeamTelecallerWorkloadContent() {
  const router = useRouter()
  const params = useParams<{ id: string }>()
  const telecallerId = params.id
  const { filters, setFilters } = useUrlFilters(FILTER_DEFAULTS)

  const summaryQuery = useTelecallerSummary(telecallerId)
  const dailyQuery = useTelecallerDailyPerformance(telecallerId)
  const query = useTelecallerOrders(telecallerId, {
    page: filters.page,
    pageSize: filters.page_size,
  })

  const summary = summaryQuery.data
  const title = summary
    ? `${summary.telecaller_name} — Telecaller Performance`
    : "Telecaller Performance"

  return (
    <>
      <PageHeader
        title={title}
        backHref="/team/telecallers"
        backLabel="Back to Telecallers"
      />

      <div className="mb-6 grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-5">
        <StatTile
          label="Total Assigned"
          value={summary?.assigned ?? "—"}
          icon={ListChecks}
          accent="slate"
        />
        <StatTile
          label="Total Attempts"
          value={summary?.total_attempts ?? "—"}
          icon={PhoneCall}
          accent="blue"
        />
        <StatTile
          label="Connected"
          value={summary?.connected ?? "—"}
          icon={UserCheck}
          accent="violet"
        />
        <StatTile
          label="Confirmed"
          value={summary?.confirmed ?? "—"}
          icon={CheckCircle2}
          accent="emerald"
        />
        <StatTile
          label="Not Interested"
          value={summary?.not_interested ?? "—"}
          icon={ThumbsDown}
          accent="orange"
        />
        <StatTile
          label="Cancelled"
          value={summary?.cancelled ?? "—"}
          icon={XCircle}
          accent="orange"
        />
        <StatTile
          label="Follow-ups"
          value={summary?.follow_ups ?? "—"}
          icon={CalendarClock}
          accent="amber"
        />
        <StatTile
          label="Orders Fulfilled"
          value={summary?.fulfilled ?? "—"}
          icon={Truck}
          accent="emerald"
        />
        <StatTile
          label="Conversion Rate"
          value={summary ? `${summary.conversion_rate}%` : "—"}
          icon={Target}
          accent="violet"
        />
      </div>

      <div className="mb-6">
        <TelecallerDailyPerformanceChart
          data={dailyQuery.data}
          isLoading={dailyQuery.isLoading}
        />
      </div>

      <QueryStates
        isLoading={query.isLoading}
        isError={query.isError}
        error={query.error}
        data={query.data}
        onRetry={() => void query.refetch()}
        isEmpty={(data) => data.data.length === 0}
        emptyTitle="No orders assigned"
      >
        {(data) => (
          <div className="flex flex-col gap-4">
            <DataTable
              columns={columns}
              data={data.data}
              rowKey={(o) => o.order_id}
              onRowClick={(o) => router.push(`/team/orders/${o.order_id}`)}
            />
            <PaginationBar
              meta={data.meta}
              onPageChange={(page) => setFilters({ page })}
              pageSizeOptions={[10, 20, 50, 100]}
              onPageSizeChange={(page_size) => setFilters({ page_size, page: 1 })}
            />
          </div>
        )}
      </QueryStates>
    </>
  )
}

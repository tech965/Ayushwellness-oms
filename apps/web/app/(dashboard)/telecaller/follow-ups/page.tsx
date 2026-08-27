"use client"

import * as React from "react"
import { useRouter } from "next/navigation"

import { DataTable, type DataTableColumn } from "@/components/shared/data-table"
import { PageHeader } from "@/components/shared/page-header"
import { PaginationBar } from "@/components/shared/pagination-bar"
import { QueryStates } from "@/components/shared/query-states"
import { StatusBadge } from "@/components/shared/status-badge"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { formatDateTime, formatMoney } from "@/lib/format"
import { useUrlFilters } from "@/lib/use-url-filters"
import { useMyFollowUps } from "@/services/telecaller"
import type { AssignedOrder } from "@/types/telecalling"

const FILTER_DEFAULTS = { when: "today", page: 1, page_size: 20 }

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
    id: "next_follow_up_at",
    header: "Follow-up Due",
    cell: (o) => (o.next_follow_up_at ? formatDateTime(o.next_follow_up_at) : "—"),
  },
]

export default function TelecallerFollowUpsPage() {
  return (
    <React.Suspense>
      <TelecallerFollowUpsContent />
    </React.Suspense>
  )
}

function TelecallerFollowUpsContent() {
  const router = useRouter()
  const { filters, setFilters } = useUrlFilters(FILTER_DEFAULTS)
  const when = filters.when as "today" | "overdue" | "upcoming"

  const query = useMyFollowUps({ when, page: filters.page, pageSize: filters.page_size })

  return (
    <>
      <PageHeader
        title="Follow-ups"
        description="What needs to be called, organized by timing."
      />
      <div className="flex flex-col gap-4">
        <Tabs value={when} onValueChange={(v) => setFilters({ when: v, page: 1 })}>
          <TabsList>
            <TabsTrigger value="today">Today</TabsTrigger>
            <TabsTrigger value="overdue">Overdue</TabsTrigger>
            <TabsTrigger value="upcoming">Upcoming</TabsTrigger>
          </TabsList>
        </Tabs>

        <QueryStates
          isLoading={query.isLoading}
          isError={query.isError}
          error={query.error}
          data={query.data}
          onRetry={() => void query.refetch()}
          isEmpty={(data) => data.data.length === 0}
          emptyTitle="No follow-ups here"
          emptyDescription="Nothing scheduled in this window."
        >
          {(data) => (
            <>
              <DataTable
                columns={columns}
                data={data.data}
                rowKey={(o) => o.order_id}
                onRowClick={(o) => router.push(`/telecaller/orders/${o.order_id}`)}
              />
              <PaginationBar
                meta={data.meta}
                onPageChange={(page) => setFilters({ page })}
                pageSizeOptions={[10, 20, 50, 100]}
                onPageSizeChange={(page_size) => setFilters({ page_size, page: 1 })}
              />
            </>
          )}
        </QueryStates>
      </div>
    </>
  )
}

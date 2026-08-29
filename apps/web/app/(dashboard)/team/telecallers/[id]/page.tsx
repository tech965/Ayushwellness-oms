"use client"

import * as React from "react"
import { useParams, useRouter } from "next/navigation"

import { DataTable, type DataTableColumn } from "@/components/shared/data-table"
import { PageHeader } from "@/components/shared/page-header"
import { PaginationBar } from "@/components/shared/pagination-bar"
import { QueryStates } from "@/components/shared/query-states"
import { StatusBadge } from "@/components/shared/status-badge"
import { formatDate, formatMoney } from "@/lib/format"
import { useUrlFilters } from "@/lib/use-url-filters"
import { useTelecallerOrders } from "@/services/team"
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

  const query = useTelecallerOrders(telecallerId, {
    page: filters.page,
    pageSize: filters.page_size,
  })

  return (
    <>
      <PageHeader
        title="Telecaller Workload"
        backHref="/team/telecallers"
        backLabel="Back to Telecallers"
      />
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

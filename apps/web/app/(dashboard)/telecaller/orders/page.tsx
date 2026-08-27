"use client"

import * as React from "react"
import { useRouter } from "next/navigation"

import { DataTable, type DataTableColumn } from "@/components/shared/data-table"
import { FilterBar } from "@/components/shared/filter-bar"
import { PageHeader } from "@/components/shared/page-header"
import { PaginationBar } from "@/components/shared/pagination-bar"
import { QueryStates } from "@/components/shared/query-states"
import { StatusBadge } from "@/components/shared/status-badge"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { formatDate, formatMoney } from "@/lib/format"
import { useUrlFilters } from "@/lib/use-url-filters"
import { useMyOrders } from "@/services/telecaller"
import { CALL_OUTCOME_OPTIONS, type AssignedOrder } from "@/types/telecalling"

const FILTER_DEFAULTS = { call_status: "", page: 1, page_size: 20 }

const CALL_STATUS_OPTIONS = [
  { value: "not_called", label: "Not Called" },
  ...CALL_OUTCOME_OPTIONS,
]

const columns: DataTableColumn<AssignedOrder>[] = [
  {
    id: "order_number",
    header: "Order",
    cell: (o) => <span className="font-medium">{o.order_number}</span>,
  },
  { id: "customer_name", header: "Customer", cell: (o) => o.customer_name ?? "—" },
  { id: "customer_phone", header: "Phone", cell: (o) => o.customer_phone ?? "—" },
  {
    id: "item_summary",
    header: "Product",
    cell: (o) => (
      <span className="block max-w-[200px] truncate">{o.item_summary ?? "—"}</span>
    ),
  },
  {
    id: "total_amount",
    header: "Amount",
    className: "text-right",
    cell: (o) => formatMoney(o.total_amount),
  },
  {
    id: "payment_type",
    header: "Payment",
    cell: (o) => <StatusBadge domain="payment" status={o.payment_type} />,
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

export default function TelecallerOrdersPage() {
  return (
    <React.Suspense>
      <TelecallerOrdersContent />
    </React.Suspense>
  )
}

function TelecallerOrdersContent() {
  const router = useRouter()
  const { filters, setFilters } = useUrlFilters(FILTER_DEFAULTS)
  const query = useMyOrders({
    page: filters.page,
    pageSize: filters.page_size,
    call_status: filters.call_status || undefined,
  })

  return (
    <>
      <PageHeader
        title="My Assigned Orders"
        description={
          query.data
            ? `${query.data.meta.total_items} orders assigned to you.`
            : "Only orders assigned to you appear here."
        }
      />
      <div className="flex flex-col gap-4">
        <FilterBar
          extra={
            <Select
              value={filters.call_status || "__all__"}
              onValueChange={(v) => setFilters({ call_status: v === "__all__" ? "" : v })}
            >
              <SelectTrigger className="w-[180px]">
                <SelectValue placeholder="Call Status" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="__all__">All call statuses</SelectItem>
                {CALL_STATUS_OPTIONS.map((o) => (
                  <SelectItem key={o.value} value={o.value}>
                    {o.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          }
        />

        <QueryStates
          isLoading={query.isLoading}
          isError={query.isError}
          error={query.error}
          data={query.data}
          onRetry={() => void query.refetch()}
          isEmpty={(data) => data.data.length === 0}
          emptyTitle="No orders assigned"
          emptyDescription="Your team leader hasn't assigned you any orders yet."
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

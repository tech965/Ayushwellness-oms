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
import { formatDateTime, formatMoney } from "@/lib/format"
import { useUrlFilters } from "@/lib/use-url-filters"
import { useMyCheckouts } from "@/services/telecaller"
import { CALL_OUTCOME_OPTIONS, type AssignedCheckout } from "@/types/telecalling"

const FILTER_DEFAULTS = { call_status: "", page: 1, page_size: 20 }

const CALL_STATUS_OPTIONS = [
  { value: "not_called", label: "Not Called" },
  ...CALL_OUTCOME_OPTIONS,
]

const columns: DataTableColumn<AssignedCheckout>[] = [
  { id: "customer_name", header: "Customer", cell: (c) => c.customer_name ?? "—" },
  { id: "customer_phone", header: "Phone", cell: (c) => c.customer_phone ?? "—" },
  {
    id: "item_summary",
    header: "Product",
    cell: (c) => (
      <span className="block max-w-[200px] truncate">{c.item_summary ?? "—"}</span>
    ),
  },
  {
    id: "total_amount",
    header: "Cart Amount",
    className: "text-right",
    cell: (c) => formatMoney(c.total_amount),
  },
  {
    id: "priority",
    header: "Priority",
    cell: (c) =>
      c.priority ? <StatusBadge domain="lead_priority" status={c.priority} /> : "—",
  },
  {
    id: "call_status",
    header: "Call Status",
    cell: (c) => (
      <StatusBadge domain="telecalling" status={c.call_status ?? "not_called"} />
    ),
  },
  {
    id: "attempt_count",
    header: "Attempts",
    className: "text-right",
    cell: (c) => c.attempt_count,
  },
  {
    id: "next_follow_up_at",
    header: "Next Follow-up",
    cell: (c) => (c.next_follow_up_at ? formatDateTime(c.next_follow_up_at) : "—"),
  },
]

export default function TelecallerCheckoutsPage() {
  return (
    <React.Suspense>
      <TelecallerCheckoutsContent />
    </React.Suspense>
  )
}

function TelecallerCheckoutsContent() {
  const router = useRouter()
  const { filters, setFilters } = useUrlFilters(FILTER_DEFAULTS)
  const query = useMyCheckouts({
    page: filters.page,
    pageSize: filters.page_size,
    call_status: filters.call_status || undefined,
  })

  return (
    <>
      <PageHeader
        title="My Abandoned Checkout Leads"
        description={
          query.data
            ? `${query.data.meta.total_items} checkout recovery leads assigned to you.`
            : "Only checkouts assigned to you appear here."
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
          emptyTitle="No checkout leads assigned"
          emptyDescription="Your team leader hasn't assigned you any abandoned checkouts yet."
        >
          {(data) => (
            <>
              <DataTable
                columns={columns}
                data={data.data}
                rowKey={(c) => c.checkout_id}
                onRowClick={(c) => router.push(`/telecaller/checkouts/${c.checkout_id}`)}
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

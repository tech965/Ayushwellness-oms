"use client"

import * as React from "react"

import { DataTable, type DataTableColumn } from "@/components/shared/data-table"
import { FilterBar } from "@/components/shared/filter-bar"
import { PageHeader } from "@/components/shared/page-header"
import { PaginationBar } from "@/components/shared/pagination-bar"
import { QueryStates } from "@/components/shared/query-states"
import { StatusBadge } from "@/components/shared/status-badge"
import { formatDate, formatMoney } from "@/lib/format"
import { useUrlFilters } from "@/lib/use-url-filters"
import { useMyNdr } from "@/services/shipment-staff"
import { NDR_STATUS_OPTIONS, type NDR } from "@/types/ndr"

const FILTER_DEFAULTS = { q: "", status: "", page: 1, page_size: 20 }

/** NDR records scoped to this Shipment Staff user's own permitted
 * Telecallers -- see `services/shipment-staff.ts`. Read-only: use the
 * shipment detail page's reattempt action (once wired) for handling one.
 */
export default function ShipmentStaffNdrPage() {
  return (
    <React.Suspense>
      <ShipmentStaffNdrContent />
    </React.Suspense>
  )
}

function ShipmentStaffNdrContent() {
  const { filters, setFilters } = useUrlFilters(FILTER_DEFAULTS)
  const query = useMyNdr({
    page: filters.page,
    pageSize: filters.page_size,
    q: filters.q || undefined,
    status: (filters.status || undefined) as NDR["status"] | undefined,
  })

  const columns: DataTableColumn<NDR>[] = [
    { id: "order_number", header: "Order", cell: (n) => n.order_number ?? "—" },
    { id: "customer_name", header: "Customer", cell: (n) => n.customer_name ?? "—" },
    { id: "customer_phone", header: "Phone", cell: (n) => n.customer_phone ?? "—" },
    { id: "reason", header: "Reason", cell: (n) => n.normalized_reason ?? n.reason ?? "—" },
    { id: "attempt_number", header: "Attempts", cell: (n) => n.attempt_number },
    {
      id: "status",
      header: "Status",
      cell: (n) => <StatusBadge domain="ndr" status={n.status} />,
    },
    {
      id: "order_amount",
      header: "Amount",
      cell: (n) => (n.order_amount ? formatMoney(n.order_amount) : "—"),
    },
    { id: "awb", header: "AWB", cell: (n) => n.awb ?? "—" },
    { id: "created_at", header: "Reported", cell: (n) => formatDate(n.created_at) },
  ]

  return (
    <>
      <PageHeader
        title="NDR"
        description={
          query.data
            ? `${query.data.meta.total_items} NDR records for your assigned Telecallers.`
            : "Non-delivery reports for your assigned Telecallers."
        }
      />
      <div className="flex flex-col gap-4">
        <FilterBar
          searchValue={filters.q}
          onSearchChange={(v) => setFilters({ q: v, page: 1 })}
          searchPlaceholder="Search by order # or customer..."
          statusValue={filters.status || undefined}
          onStatusChange={(v) => setFilters({ status: v ?? "", page: 1 })}
          statusOptions={NDR_STATUS_OPTIONS}
        />
        <QueryStates
          isLoading={query.isLoading}
          isError={query.isError}
          error={query.error}
          data={query.data}
          onRetry={() => void query.refetch()}
          isEmpty={(data) => data.data.length === 0}
          emptyTitle="No NDR records"
          emptyDescription="Non-delivery reports for your assigned Telecallers will appear here."
        >
          {(data) => (
            <>
              <DataTable columns={columns} data={data.data} rowKey={(n) => n.id} />
              <PaginationBar meta={data.meta} onPageChange={(page) => setFilters({ page })} />
            </>
          )}
        </QueryStates>
      </div>
    </>
  )
}

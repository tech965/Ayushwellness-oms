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
import { useMyRto } from "@/services/shipment-staff"
import { RTO_STATUS_OPTIONS, type RTO } from "@/types/rto"

const FILTER_DEFAULTS = { q: "", status: "", page: 1, page_size: 20 }

/** RTO records scoped to this Shipment Staff user's own permitted
 * Telecallers -- see `services/shipment-staff.ts`.
 */
export default function ShipmentStaffRtoPage() {
  return (
    <React.Suspense>
      <ShipmentStaffRtoContent />
    </React.Suspense>
  )
}

function ShipmentStaffRtoContent() {
  const { filters, setFilters } = useUrlFilters(FILTER_DEFAULTS)
  const query = useMyRto({
    page: filters.page,
    pageSize: filters.page_size,
    q: filters.q || undefined,
    status: (filters.status || undefined) as RTO["status"] | undefined,
  })

  const columns: DataTableColumn<RTO>[] = [
    { id: "order_number", header: "Order", cell: (r) => r.order_number ?? "—" },
    { id: "customer_name", header: "Customer", cell: (r) => r.customer_name ?? "—" },
    { id: "customer_phone", header: "Phone", cell: (r) => r.customer_phone ?? "—" },
    { id: "reason", header: "Reason", cell: (r) => r.normalized_reason ?? r.reason ?? "—" },
    {
      id: "status",
      header: "Status",
      cell: (r) => <StatusBadge domain="rto" status={r.status} />,
    },
    {
      id: "order_amount",
      header: "Amount",
      cell: (r) => (r.order_amount ? formatMoney(r.order_amount) : "—"),
    },
    { id: "awb", header: "AWB", cell: (r) => r.awb ?? "—" },
    { id: "created_at", header: "Initiated", cell: (r) => formatDate(r.created_at) },
  ]

  return (
    <>
      <PageHeader
        title="RTO"
        description={
          query.data
            ? `${query.data.meta.total_items} RTO records for your assigned Telecallers.`
            : "Return-to-origin records for your assigned Telecallers."
        }
      />
      <div className="flex flex-col gap-4">
        <FilterBar
          searchValue={filters.q}
          onSearchChange={(v) => setFilters({ q: v, page: 1 })}
          searchPlaceholder="Search by order # or customer..."
          statusValue={filters.status || undefined}
          onStatusChange={(v) => setFilters({ status: v ?? "", page: 1 })}
          statusOptions={RTO_STATUS_OPTIONS}
        />
        <QueryStates
          isLoading={query.isLoading}
          isError={query.isError}
          error={query.error}
          data={query.data}
          onRetry={() => void query.refetch()}
          isEmpty={(data) => data.data.length === 0}
          emptyTitle="No RTO records"
          emptyDescription="Return-to-origin records for your assigned Telecallers will appear here."
        >
          {(data) => (
            <>
              <DataTable columns={columns} data={data.data} rowKey={(r) => r.id} />
              <PaginationBar meta={data.meta} onPageChange={(page) => setFilters({ page })} />
            </>
          )}
        </QueryStates>
      </div>
    </>
  )
}

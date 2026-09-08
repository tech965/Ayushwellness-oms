"use client"

import * as React from "react"
import { useRouter } from "next/navigation"

import { DataTable, type DataTableColumn } from "@/components/shared/data-table"
import { FilterBar, type DateRangeValue } from "@/components/shared/filter-bar"
import { PageHeader } from "@/components/shared/page-header"
import { PaginationBar } from "@/components/shared/pagination-bar"
import { QueryStates } from "@/components/shared/query-states"
import { StatusBadge } from "@/components/shared/status-badge"
import { formatDate } from "@/lib/format"
import { usePaginationState } from "@/lib/use-pagination"
import { useMyShipments } from "@/services/shipment-staff"
import { SHIPMENT_STATUS_OPTIONS, type Shipment, type ShipmentStatus } from "@/types/shipment"

/** Every already-processed shipment for orders confirmed by this
 * Shipment Staff user's own permitted Telecallers -- the scoped
 * counterpart to the Admin/Operations `/shipments` list.
 */
export default function ShipmentStaffShipmentsPage() {
  const router = useRouter()
  const { page, pageSize, setPage, resetPage } = usePaginationState()
  const [search, setSearch] = React.useState("")
  const [status, setStatus] = React.useState<ShipmentStatus | undefined>(undefined)
  const [dateRange, setDateRange] = React.useState<DateRangeValue>({})

  const query = useMyShipments({
    page,
    pageSize,
    q: search,
    status,
    date_from: dateRange.from?.toISOString(),
    date_to: dateRange.to?.toISOString(),
  })

  const columns: DataTableColumn<Shipment>[] = [
    { id: "awb", header: "AWB", cell: (shipment) => shipment.awb ?? "—" },
    {
      id: "status",
      header: "Status",
      cell: (shipment) => <StatusBadge domain="shipment" status={shipment.current_status} />,
    },
    {
      id: "location",
      header: "Current location",
      cell: (shipment) => shipment.current_location ?? "—",
    },
    {
      id: "expected",
      header: "Expected delivery",
      cell: (shipment) =>
        shipment.expected_delivery_date ? formatDate(shipment.expected_delivery_date) : "—",
    },
    {
      id: "delay",
      header: "Delay",
      cell: (shipment) => <StatusBadge domain="shipment_delay" status={shipment.delay_status} />,
    },
  ]

  return (
    <>
      <PageHeader title="Shipments" description="AWB search, status/date filters." />
      <div className="flex flex-col gap-4">
        <FilterBar
          searchValue={search}
          onSearchChange={(value) => {
            setSearch(value)
            resetPage()
          }}
          searchPlaceholder="Search by AWB..."
          statusValue={status}
          onStatusChange={(value) => {
            setStatus(value as ShipmentStatus | undefined)
            resetPage()
          }}
          statusOptions={SHIPMENT_STATUS_OPTIONS}
          statusLabel="Status"
          dateRange={dateRange}
          onDateRangeChange={(range) => {
            setDateRange(range)
            resetPage()
          }}
        />
        <QueryStates
          isLoading={query.isLoading}
          isError={query.isError}
          error={query.error}
          data={query.data}
          onRetry={() => void query.refetch()}
          isEmpty={(data) => data.data.length === 0}
          emptyTitle="No shipments found"
          emptyDescription="Try adjusting your search or filters."
        >
          {(data) => (
            <>
              <DataTable
                columns={columns}
                data={data.data}
                rowKey={(shipment) => shipment.id}
                onRowClick={(shipment) => router.push(`/shipment-staff/shipments/${shipment.id}`)}
              />
              <PaginationBar meta={data.meta} onPageChange={setPage} />
            </>
          )}
        </QueryStates>
      </div>
    </>
  )
}

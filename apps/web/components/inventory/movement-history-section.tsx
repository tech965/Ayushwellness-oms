"use client"

import * as React from "react"

import { DataTable, type DataTableColumn } from "@/components/shared/data-table"
import { FilterBar, type DateRangeValue } from "@/components/shared/filter-bar"
import { PaginationBar } from "@/components/shared/pagination-bar"
import { QueryStates } from "@/components/shared/query-states"
import { formatDateTime } from "@/lib/format"
import { usePaginationState } from "@/lib/use-pagination"
import { useInventoryMovements } from "@/services/inventory"
import {
  INVENTORY_MOVEMENT_TYPE_OPTIONS,
  type InventoryMovement,
  type InventoryMovementType,
} from "@/types/inventory"

function movementTypeLabel(type: InventoryMovement["movement_type"]): string {
  return INVENTORY_MOVEMENT_TYPE_OPTIONS.find((option) => option.value === type)?.label ?? type
}

/** Module 1.1 — catalogue-wide "SKU-wise movement history" (item 7),
 * with search/filter/date-range (item 9). Every row is a real, existing
 * `InventoryMovement` ledger entry — the same ledger Module 1.2's
 * dispatch/RTO/cancellation logic writes to, read-only here.
 */
export function MovementHistorySection() {
  const { page, pageSize, setPage, resetPage } = usePaginationState()
  const [sku, setSku] = React.useState("")
  const [movementType, setMovementType] = React.useState<InventoryMovementType | undefined>(
    undefined
  )
  const [dateRange, setDateRange] = React.useState<DateRangeValue>({})

  const query = useInventoryMovements({
    page,
    pageSize,
    sku: sku || undefined,
    movement_type: movementType,
    date_from: dateRange.from ? dateRange.from.toISOString() : undefined,
    date_to: dateRange.to ? dateRange.to.toISOString() : undefined,
  })

  const columns: DataTableColumn<InventoryMovement>[] = [
    { id: "when", header: "Date/time", cell: (row) => formatDateTime(row.created_at) },
    { id: "product", header: "Product", cell: (row) => row.product_title ?? "—" },
    { id: "sku", header: "SKU", cell: (row) => row.sku ?? "—" },
    {
      id: "movement",
      header: "Movement",
      cell: (row) => {
        const signed = row.quantity_delta > 0 ? `+${row.quantity_delta}` : `${row.quantity_delta}`
        return (
          <span className={row.quantity_delta < 0 ? "text-red-600" : "text-emerald-600"}>
            {movementTypeLabel(row.movement_type)}: {signed}
          </span>
        )
      },
    },
    { id: "balance", header: "Balance After", cell: (row) => row.quantity_after },
    {
      id: "reference",
      header: "Reference",
      cell: (row) =>
        row.shipment_id ? "Shipment" : row.rto_id ? "RTO" : row.order_id ? "Order" : "—",
    },
    { id: "reason", header: "Reason", cell: (row) => row.reason ?? "—" },
    { id: "actor", header: "Actor/source", cell: (row) => row.actor_label },
  ]

  return (
    <div className="flex flex-col gap-3">
      <h2 className="text-lg font-semibold">Movement History</h2>
      <FilterBar
        searchValue={sku}
        onSearchChange={(value) => {
          setSku(value)
          resetPage()
        }}
        searchPlaceholder="Search by SKU..."
        statusValue={movementType}
        onStatusChange={(value) => {
          setMovementType(value as InventoryMovementType | undefined)
          resetPage()
        }}
        statusOptions={INVENTORY_MOVEMENT_TYPE_OPTIONS}
        statusLabel="Movement type"
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
        emptyTitle="No stock movements found"
        emptyDescription="Dispatches, RTO restocks, and manual adjustments will appear here."
      >
        {(data) => (
          <>
            <DataTable columns={columns} data={data.data} rowKey={(row) => row.id} />
            <PaginationBar meta={data.meta} onPageChange={setPage} />
          </>
        )}
      </QueryStates>
    </div>
  )
}

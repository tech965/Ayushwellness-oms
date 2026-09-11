"use client"

import * as React from "react"
import { useRouter } from "next/navigation"

import { BulkShipDialog } from "@/components/fulfillment/bulk-ship-dialog"
import { ShipmentActionCell } from "@/components/fulfillment/shipment-action-cell"
import { AddressValidationBadge } from "@/components/shared/address-validation-badge"
import { DataTable, type DataTableColumn } from "@/components/shared/data-table"
import type { DateRangeValue } from "@/components/shared/date-range-picker"
import { FilterBar } from "@/components/shared/filter-bar"
import { PageHeader } from "@/components/shared/page-header"
import { PaginationBar } from "@/components/shared/pagination-bar"
import { QueryStates } from "@/components/shared/query-states"
import { StatusBadge } from "@/components/shared/status-badge"
import { Button } from "@/components/ui/button"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { formatDate, formatMoney } from "@/lib/format"
import { useUrlFilters } from "@/lib/use-url-filters"
import { useShipmentQueue } from "@/services/shipment-queue"
import { useTeamTelecallers } from "@/services/team"
import { PAYMENT_TYPE_OPTIONS } from "@/types/order"
import {
  SHIPMENT_STATUS_OPTIONS,
  type ShipmentQueueRow,
  type ShipmentStatus,
} from "@/types/shipment"

const FILTER_DEFAULTS = {
  q: "",
  payment_type: "",
  telecaller_id: "",
  shipment_status: "",
  date_from: "",
  date_to: "",
  page: 1,
  page_size: 20,
}

/** The Fulfillment / Shipment Queue body — confirmed orders awaiting
 * shipment processing. A distinct, narrower view from the plain
 * `/shipments` list (already-processed shipments). Rendered both at
 * `/fulfillment/orders` and at the legacy `/shipment-queue` alias.
 */
export function FulfillmentQueue() {
  return (
    <React.Suspense>
      <FulfillmentQueueContent />
    </React.Suspense>
  )
}

function FulfillmentQueueContent() {
  const router = useRouter()
  const { filters, setFilters, clearFilters } = useUrlFilters(FILTER_DEFAULTS)
  const telecallersQuery = useTeamTelecallers()

  const [selectedIds, setSelectedIds] = React.useState<Set<string>>(new Set())
  const [bulkShipOpen, setBulkShipOpen] = React.useState(false)

  function toggleOne(id: string) {
    setSelectedIds((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  function toggleAllOnPage(ids: string[]) {
    setSelectedIds((prev) => {
      const allSelected = ids.every((id) => prev.has(id))
      const next = new Set(prev)
      for (const id of ids) {
        if (allSelected) next.delete(id)
        else next.add(id)
      }
      return next
    })
  }

  const dateRange: DateRangeValue = {
    from: filters.date_from ? new Date(filters.date_from) : undefined,
    to: filters.date_to ? new Date(filters.date_to) : undefined,
  }

  const query = useShipmentQueue({
    page: filters.page,
    pageSize: filters.page_size,
    q: filters.q || undefined,
    payment_type: filters.payment_type || undefined,
    telecaller_id: filters.telecaller_id || undefined,
    shipment_status: (filters.shipment_status || undefined) as ShipmentStatus | undefined,
    date_from: filters.date_from || undefined,
    date_to: filters.date_to || undefined,
  })

  const columns: DataTableColumn<ShipmentQueueRow>[] = [
    {
      id: "order_number",
      header: "Order #",
      cell: (r) => <span className="font-medium">{r.order_number}</span>,
    },
    {
      id: "customer_name",
      header: "Customer",
      cell: (r) => (
        <div className="flex flex-col">
          <span>{r.customer_name ?? "—"}</span>
          {r.customer_phone && (
            <span className="text-muted-foreground text-xs">{r.customer_phone}</span>
          )}
        </div>
      ),
    },
    {
      id: "item_summary",
      header: "Items",
      cell: (r) => <span className="block max-w-[240px] truncate">{r.item_summary ?? "—"}</span>,
    },
    {
      id: "total_amount",
      header: "Amount",
      className: "text-right",
      cell: (r) => formatMoney(r.total_amount),
    },
    {
      id: "payment_type",
      header: "Payment",
      cell: (r) => (
        <div className="flex items-center gap-1.5">
          <StatusBadge domain="payment" status={r.payment_type} />
          <StatusBadge domain="payment" status={r.payment_status} />
        </div>
      ),
    },
    {
      id: "confirmed_by_telecaller_name",
      header: "Confirmed By",
      cell: (r) => (
        <span className="font-medium">{r.confirmed_by_telecaller_name ?? "—"}</span>
      ),
    },
    {
      id: "confirmed_at",
      header: "Confirmed At",
      cell: (r) => (r.confirmed_at ? formatDate(r.confirmed_at) : "—"),
    },
    {
      id: "shipment_status",
      header: "Shipment Status",
      cell: (r) =>
        r.shipment_status ? (
          <StatusBadge domain="shipment" status={r.shipment_status} />
        ) : (
          <span className="text-muted-foreground text-xs">Ready to Ship</span>
        ),
    },
    {
      id: "address_validation",
      header: "Address Validation",
      // Primary location for this: the fulfillment user must be able to
      // spot a problematic address BEFORE clicking "Process Shipment" --
      // sourced straight off this row's already-loaded `Order` data
      // (see `ShipmentQueueRowResponse` on the backend), so showing it
      // here costs zero extra queries and no external calls per row.
      cell: (r) => (
        <AddressValidationBadge
          status={r.shipping_address_validation_status}
          score={r.shipping_address_validation_score}
        />
      ),
    },
    {
      id: "actions",
      header: "",
      cell: (r) => (
        <ShipmentActionCell
          orderId={r.order_id}
          shipmentId={r.shipment_id}
          shipmentStatus={r.shipment_status}
          shopifySyncStatus={r.shopify_sync_status}
          orderStatus="confirmed"
          fulfillmentStatus="unfulfilled"
        />
      ),
    },
  ]

  return (
    <>
      <PageHeader
        title="Orders Need Shipment"
        description={
          query.data
            ? `${query.data.meta.total_items} confirmed orders ready for shipment.`
            : "Confirmed orders ready for shipment."
        }
      />
      <div className="flex flex-col gap-4">
        <FilterBar
          searchValue={filters.q}
          onSearchChange={(v) => setFilters({ q: v, page: 1 })}
          searchPlaceholder="Search by order # or customer..."
          statusValue={filters.shipment_status || undefined}
          onStatusChange={(v) => setFilters({ shipment_status: v ?? "", page: 1 })}
          statusOptions={SHIPMENT_STATUS_OPTIONS}
          statusLabel="Shipment Status"
          dateRange={dateRange}
          onDateRangeChange={(range) =>
            setFilters({
              date_from: range.from ? range.from.toISOString() : "",
              date_to: range.to ? range.to.toISOString() : "",
              page: 1,
            })
          }
          extra={
            <>
              <Select
                value={filters.payment_type || "__all__"}
                onValueChange={(v) =>
                  setFilters({ payment_type: v === "__all__" ? "" : v, page: 1 })
                }
              >
                <SelectTrigger className="w-[150px]">
                  <SelectValue placeholder="Payment Type" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="__all__">All payment types</SelectItem>
                  {PAYMENT_TYPE_OPTIONS.map((o) => (
                    <SelectItem key={o.value} value={o.value}>
                      {o.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <Select
                value={filters.telecaller_id || "__all__"}
                onValueChange={(v) =>
                  setFilters({ telecaller_id: v === "__all__" ? "" : v, page: 1 })
                }
              >
                <SelectTrigger className="w-[180px]">
                  <SelectValue placeholder="Telecaller" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="__all__">All telecallers</SelectItem>
                  {telecallersQuery.data?.map((t) => (
                    <SelectItem key={t.telecaller_id} value={t.telecaller_id}>
                      {t.telecaller_name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </>
          }
        />
        {(filters.q ||
          filters.payment_type ||
          filters.telecaller_id ||
          filters.shipment_status ||
          filters.date_from ||
          filters.date_to) && (
          <Button variant="ghost" size="sm" className="w-fit" onClick={clearFilters}>
            Clear all filters
          </Button>
        )}

        {selectedIds.size > 0 && (
          <div className="bg-muted/50 border-border flex items-center justify-between rounded-lg border px-4 py-2 text-sm">
            <span className="font-medium">Selected {selectedIds.size} orders</span>
            <div className="flex items-center gap-2">
              <Button size="sm" onClick={() => setBulkShipOpen(true)}>
                Process Shipment
              </Button>
              <Button variant="ghost" size="sm" onClick={() => setSelectedIds(new Set())}>
                Clear Selection
              </Button>
            </div>
          </div>
        )}

        <QueryStates
          isLoading={query.isLoading}
          isError={query.isError}
          error={query.error}
          data={query.data}
          onRetry={() => void query.refetch()}
          isEmpty={(data) => data.data.length === 0}
          emptyTitle="No confirmed orders awaiting shipment"
          emptyDescription="Confirmed orders will appear here once a telecaller confirms them."
        >
          {(data) => (
            <>
              <DataTable
                columns={columns}
                data={data.data}
                rowKey={(r) => r.order_id}
                onRowClick={(r) => router.push(`/orders/${r.order_id}`)}
                selection={{
                  selectedIds,
                  onToggle: toggleOne,
                  onToggleAll: toggleAllOnPage,
                }}
              />
              <PaginationBar
                meta={data.meta}
                onPageChange={(page) => setFilters({ page })}
                pageSizeOptions={[10, 20, 50, 100]}
                onPageSizeChange={(page_size) => setFilters({ page_size, page: 1 })}
              />
              <BulkShipDialog
                open={bulkShipOpen}
                onOpenChange={setBulkShipOpen}
                rows={data.data
                  .filter((r) => selectedIds.has(r.order_id))
                  .map((r) => ({ id: r.order_id, order_number: r.order_number }))}
                onDone={() => setSelectedIds(new Set())}
              />
            </>
          )}
        </QueryStates>
      </div>
    </>
  )
}

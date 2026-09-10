"use client"

import * as React from "react"
import { useRouter } from "next/navigation"

import { BulkShipDialog } from "@/components/fulfillment/bulk-ship-dialog"
import { ShipmentActionCell } from "@/components/fulfillment/shipment-action-cell"
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
import { useTelecallerConfirmedOrders } from "@/services/orders"
import { useTeamTelecallers } from "@/services/team"
import {
  ORDER_STATUS_OPTIONS,
  PAYMENT_TYPE_OPTIONS,
  type Order,
  type OrderStatus,
} from "@/types/order"
import { SHIPMENT_STATUS_OPTIONS, type ShipmentStatus } from "@/types/shipment"

const FILTER_DEFAULTS = {
  q: "",
  status: "",
  payment_type: "",
  telecaller_id: "",
  shipment_status: "",
  confirmed_date_from: "",
  confirmed_date_to: "",
  page: 1,
  page_size: 20,
}

/** "Confirmed by Telecaller" -- a history/operational view of every order
 * Telecalling has ever confirmed (`confirmed_by_telecaller_id IS NOT
 * NULL`, via `useTelecallerConfirmedOrders`), independent of current
 * order/fulfillment/shipment status. Deliberately NOT the same query or
 * filtering logic as `FulfillmentQueue` ("Orders Need Shipment") -- that
 * page answers "what still needs shipping"; this one answers "what has
 * Telecalling confirmed," and a shipped/delivered order stays visible
 * here (see `useTelecallerConfirmedOrders`'s docstring).
 */
export function ConfirmedByTelecaller() {
  return (
    <React.Suspense>
      <ConfirmedByTelecallerContent />
    </React.Suspense>
  )
}

function ConfirmedByTelecallerContent() {
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

  const confirmedDateRange: DateRangeValue = {
    from: filters.confirmed_date_from ? new Date(filters.confirmed_date_from) : undefined,
    to: filters.confirmed_date_to ? new Date(filters.confirmed_date_to) : undefined,
  }

  const query = useTelecallerConfirmedOrders({
    page: filters.page,
    pageSize: filters.page_size,
    sortBy: "confirmed_at",
    sortOrder: "desc",
    q: filters.q || undefined,
    status: (filters.status || undefined) as OrderStatus | undefined,
    payment_type: (filters.payment_type || undefined) as Order["payment_type"] | undefined,
    telecaller_id: filters.telecaller_id || undefined,
    shipment_status: filters.shipment_status || undefined,
    confirmed_date_from: filters.confirmed_date_from || undefined,
    confirmed_date_to: filters.confirmed_date_to || undefined,
  })

  const columns: DataTableColumn<Order>[] = [
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
      cell: (r) => <span className="block max-w-[220px] truncate">{r.item_summary ?? "—"}</span>,
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
      // Every row here is filtered server-side to
      // `confirmed_by_telecaller_id IS NOT NULL` -- a real Telecaller
      // confirmed it, so a missing name here would mean the relationship
      // failed to resolve, not that confirmation never happened. Never
      // silently falls back to "—" the way an optional field elsewhere
      // would; a `null` name for a `NOT NULL` id is a bug worth a visibly
      // different marker in that (never-expected) case.
      cell: (r) => (
        <span className="font-medium">
          {r.confirmed_by_telecaller_name ?? "Unknown Telecaller"}
        </span>
      ),
    },
    {
      id: "confirmed_at",
      header: "Confirmed At",
      cell: (r) => (r.confirmed_at ? formatDate(r.confirmed_at) : "—"),
    },
    {
      id: "status",
      header: "Order Status",
      cell: (r) => <StatusBadge domain="order" status={r.status} />,
    },
    {
      id: "fulfillment_status",
      header: "Fulfillment Status",
      cell: (r) => <StatusBadge domain="fulfillment" status={r.fulfillment_status} />,
    },
    {
      id: "shipment_status",
      header: "Shipment Status",
      cell: (r) =>
        r.shipment_status ? (
          <StatusBadge domain="shipment" status={r.shipment_status} />
        ) : (
          <span className="text-muted-foreground text-xs">Not shipped</span>
        ),
    },
    {
      id: "actions",
      header: "",
      cell: (r) => (
        <ShipmentActionCell
          orderId={r.id}
          shipmentId={r.shipment_id}
          shipmentStatus={r.shipment_status}
          shopifySyncStatus={r.shopify_sync_status}
          orderStatus={r.status}
          fulfillmentStatus={r.fulfillment_status}
          shiprocketOrderUrl={r.shiprocket_order_url}
        />
      ),
    },
  ]

  const hasActiveFilters =
    filters.q ||
    filters.status ||
    filters.payment_type ||
    filters.telecaller_id ||
    filters.shipment_status ||
    filters.confirmed_date_from ||
    filters.confirmed_date_to

  return (
    <>
      <PageHeader
        title="Confirmed by Telecaller"
        description={
          query.data
            ? `${query.data.meta.total_items} orders confirmed by Telecalling.`
            : "Orders confirmed by Telecalling."
        }
      />
      <div className="flex flex-col gap-4">
        <FilterBar
          searchValue={filters.q}
          onSearchChange={(v) => setFilters({ q: v, page: 1 })}
          searchPlaceholder="Search by order # or customer..."
          statusValue={filters.status || undefined}
          onStatusChange={(v) => setFilters({ status: v ?? "", page: 1 })}
          statusOptions={ORDER_STATUS_OPTIONS}
          statusLabel="Order Status"
          dateRange={confirmedDateRange}
          onDateRangeChange={(range) =>
            setFilters({
              confirmed_date_from: range.from ? range.from.toISOString() : "",
              confirmed_date_to: range.to ? range.to.toISOString() : "",
              page: 1,
            })
          }
          extra={
            <>
              <Select
                value={filters.shipment_status || "__all__"}
                onValueChange={(v) =>
                  setFilters({ shipment_status: v === "__all__" ? "" : v, page: 1 })
                }
              >
                <SelectTrigger className="w-[160px]">
                  <SelectValue placeholder="Shipment Status" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="__all__">All shipment statuses</SelectItem>
                  {SHIPMENT_STATUS_OPTIONS.map((o) => (
                    <SelectItem key={o.value} value={o.value}>
                      {o.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
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
        {hasActiveFilters && (
          <Button variant="ghost" size="sm" className="w-fit" onClick={clearFilters}>
            Clear all filters
          </Button>
        )}

        {selectedIds.size > 0 && (
          <div className="bg-muted/50 border-border flex items-center justify-between rounded-lg border px-4 py-2 text-sm">
            <span className="font-medium">Selected {selectedIds.size} orders</span>
            <div className="flex items-center gap-2">
              <Button size="sm" onClick={() => setBulkShipOpen(true)}>
                Open in Shiprocket
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
          emptyTitle="No Telecaller-confirmed orders yet"
          emptyDescription="Orders confirmed by Telecalling will appear here, for as long as they exist -- shipped and delivered orders stay listed too."
        >
          {(data) => (
            <>
              <DataTable
                columns={columns}
                data={data.data}
                rowKey={(r) => r.id}
                onRowClick={(r) => router.push(`/orders/${r.id}`)}
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
                  .filter((r) => selectedIds.has(r.id))
                  .map((r) => ({ id: r.id, order_number: r.order_number }))}
                onDone={() => setSelectedIds(new Set())}
              />
            </>
          )}
        </QueryStates>
      </div>
    </>
  )
}

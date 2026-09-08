"use client"

import * as React from "react"
import { useRouter } from "next/navigation"
import { toast } from "sonner"

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
import { getApiErrorMessage } from "@/lib/api-client"
import { formatDate, formatMoney } from "@/lib/format"
import { useUrlFilters } from "@/lib/use-url-filters"
import { useMyConfirmedOrders, useShipMyConfirmedOrder } from "@/services/shipment-staff"
import { PAYMENT_TYPE_OPTIONS } from "@/types/order"
import {
  SHIPMENT_STATUS_OPTIONS,
  type ShipmentQueueRow,
  type ShipmentStatus,
} from "@/types/shipment"

const FILTER_DEFAULTS = {
  q: "",
  payment_type: "",
  shipment_status: "",
  date_from: "",
  date_to: "",
  page: 1,
  page_size: 20,
}

/** Confirmed Orders / Shipment Queue, scoped to the Shipment Staff
 * caller's own permitted Telecallers -- see `services/shipment-staff.ts`.
 * No telecaller filter dropdown (unlike the Fulfillment/Admin queue):
 * every order here already belongs to one of this Shipment Staff user's
 * own Telecallers, so filtering further by telecaller within that small
 * set adds little value and would need an org-wide telecaller roster
 * this role deliberately has no permission to fetch.
 */
export default function ShipmentStaffOrdersPage() {
  return (
    <React.Suspense>
      <ShipmentStaffOrdersContent />
    </React.Suspense>
  )
}

function ShipmentStaffOrdersContent() {
  const router = useRouter()
  const { filters, setFilters, clearFilters } = useUrlFilters(FILTER_DEFAULTS)
  const shipOrder = useShipMyConfirmedOrder()

  const dateRange: DateRangeValue = {
    from: filters.date_from ? new Date(filters.date_from) : undefined,
    to: filters.date_to ? new Date(filters.date_to) : undefined,
  }

  const query = useMyConfirmedOrders({
    page: filters.page,
    pageSize: filters.page_size,
    q: filters.q || undefined,
    payment_type: filters.payment_type || undefined,
    shipment_status: (filters.shipment_status || undefined) as ShipmentStatus | undefined,
    date_from: filters.date_from || undefined,
    date_to: filters.date_to || undefined,
  })

  const columns: DataTableColumn<ShipmentQueueRow>[] = [
    {
      id: "order_number",
      header: "Order",
      cell: (r) => <span className="font-medium">{r.order_number}</span>,
    },
    { id: "customer_name", header: "Customer", cell: (r) => r.customer_name ?? "—" },
    { id: "customer_phone", header: "Phone", cell: (r) => r.customer_phone ?? "—" },
    {
      id: "item_summary",
      header: "Products",
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
      cell: (r) => <StatusBadge domain="payment" status={r.payment_type} />,
    },
    {
      id: "confirmed_by_telecaller_name",
      header: "Telecaller",
      cell: (r) => r.confirmed_by_telecaller_name ?? "—",
    },
    {
      id: "confirmed_at",
      header: "Confirmed",
      cell: (r) => (r.confirmed_at ? formatDate(r.confirmed_at) : "—"),
    },
    {
      id: "shipment_status",
      header: "Shipment Status",
      cell: (r) =>
        r.shipment_status ? (
          <StatusBadge domain="shipment" status={r.shipment_status} />
        ) : (
          <span className="text-muted-foreground text-xs">Not created</span>
        ),
    },
    { id: "awb", header: "AWB", cell: (r) => r.awb ?? "—" },
    { id: "courier_name", header: "Courier", cell: (r) => r.courier_name ?? "—" },
    {
      id: "actions",
      header: "",
      cell: (r) =>
        r.shipment_id ? (
          <Button
            size="sm"
            variant="outline"
            onClick={() => router.push(`/shipment-staff/shipments/${r.shipment_id}`)}
          >
            Process Shipment
          </Button>
        ) : (
          <Button
            size="sm"
            variant="outline"
            disabled={shipOrder.isPending && shipOrder.variables === r.order_id}
            onClick={() => {
              shipOrder.mutate(r.order_id, {
                onSuccess: () => toast.success("Shipment created via Shiprocket."),
                onError: (error) => toast.error(getApiErrorMessage(error)),
              })
            }}
          >
            {shipOrder.isPending && shipOrder.variables === r.order_id
              ? "Shipping..."
              : "Ship via Shiprocket"}
          </Button>
        ),
    },
  ]

  return (
    <>
      <PageHeader
        title="Confirmed Orders"
        description={
          query.data
            ? `${query.data.meta.total_items} confirmed orders awaiting shipment processing.`
            : "Confirmed orders waiting for shipment processing."
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
            <Select
              value={filters.payment_type || "__all__"}
              onValueChange={(v) => setFilters({ payment_type: v === "__all__" ? "" : v, page: 1 })}
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
          }
        />
        {(filters.q ||
          filters.payment_type ||
          filters.shipment_status ||
          filters.date_from ||
          filters.date_to) && (
          <Button variant="ghost" size="sm" className="w-fit" onClick={clearFilters}>
            Clear all filters
          </Button>
        )}

        <QueryStates
          isLoading={query.isLoading}
          isError={query.isError}
          error={query.error}
          data={query.data}
          onRetry={() => void query.refetch()}
          isEmpty={(data) => data.data.length === 0}
          emptyTitle="No confirmed orders awaiting shipment"
          emptyDescription="Orders confirmed by your assigned Telecallers will appear here."
        >
          {(data) => (
            <>
              <DataTable columns={columns} data={data.data} rowKey={(r) => r.order_id} />
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

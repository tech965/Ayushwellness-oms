"use client"

import * as React from "react"
import { ExternalLink } from "lucide-react"

import { ProcessShipmentDialog } from "@/components/fulfillment/process-shipment-dialog"
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
import {
  useMyConfirmedOrders,
  useProcessExistingShipmentsForMyScope,
} from "@/services/shipment-staff"
import { PAYMENT_TYPE_OPTIONS } from "@/types/order"
import {
  SHIPMENT_STATUS_OPTIONS,
  type ProcessExistingShipmentResult,
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
  const { filters, setFilters, clearFilters } = useUrlFilters(FILTER_DEFAULTS)
  const processShipment = useProcessExistingShipmentsForMyScope()
  const [dialogOpen, setDialogOpen] = React.useState(false)
  const [dialogResult, setDialogResult] = React.useState<ProcessExistingShipmentResult | null>(
    null
  )
  // Keyed per order id so processing one row never disables another's
  // button.
  const [processingOrderId, setProcessingOrderId] = React.useState<string | null>(null)

  // Calls the API equivalent of Shiprocket's own dashboard "Bulk Ship
  // Orders" action for this one order -- resolves its EXISTING Shiprocket
  // shipment server-side (unchanged `locate_shiprocket_orders`) and
  // assigns an AWB, skipping if one is already on file. Never a
  // Shiprocket create-shipment API call.
  function handleProcessShipment(orderId: string) {
    setProcessingOrderId(orderId)
    setDialogResult(null)
    setDialogOpen(true)
    processShipment.mutate([orderId], {
      onSuccess: (data) => {
        setProcessingOrderId(null)
        setDialogResult(data.results[0] ?? null)
      },
      onError: (error) => {
        setProcessingOrderId(null)
        setDialogResult({
          order_id: orderId,
          order_number: null,
          status: "failed",
          shiprocket_shipment_id: null,
          shiprocket_order_id: null,
          awb: null,
          courier_name: null,
          reason: getApiErrorMessage(error),
        })
      },
    })
  }

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
      // "Ship Order"/"Process Shipment" -- the API equivalent of
      // Shiprocket's own dashboard "Bulk Ship Orders" action -- never a
      // Shiprocket create-shipment API call from here (Shiprocket may
      // already have this order, e.g. via its own Shopify channel
      // connector, independent of this OMS, so creating one here risked
      // a real duplicate-shipment bug). See `ShipmentActionCell`
      // (Fulfillment's equivalent table) for the identical rule -- kept
      // as a small local duplicate rather than reusing that component
      // directly, since it also renders a "View" button pointed at
      // `/orders/{id}`, a page this Shipment Staff role has no
      // permission to open.
      cell: (r) => {
        const isProcessing = processingOrderId === r.order_id
        return r.shipment_id ? (
          <Button
            size="sm"
            variant="outline"
            disabled={isProcessing}
            onClick={() => handleProcessShipment(r.order_id)}
          >
            <ExternalLink className="size-3.5" />
            {isProcessing ? "Processing..." : "Process Shipment"}
          </Button>
        ) : (
          <Button size="sm" disabled={isProcessing} onClick={() => handleProcessShipment(r.order_id)}>
            <ExternalLink className="size-3.5" />
            {isProcessing ? "Processing..." : "Ship Order"}
          </Button>
        )
      },
    },
  ]

  return (
    <>
      <PageHeader
        title="Orders Need Shipment"
        description={
          query.data
            ? `${query.data.meta.total_items} orders confirmed by Telecalling and ready for shipment.`
            : "Orders confirmed by Telecalling and ready for shipment."
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
      <ProcessShipmentDialog
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        isPending={processShipment.isPending}
        result={dialogResult}
      />
    </>
  )
}

"use client"

import * as React from "react"
import { useRouter } from "next/navigation"
import { Truck } from "lucide-react"
import { toast } from "sonner"

import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
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
  useBulkShipOrders,
  useShipmentQueue,
  useShipOrderFromQueue,
} from "@/services/shipment-queue"
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
  const shipOrder = useShipOrderFromQueue()
  const bulkShip = useBulkShipOrders()

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

  function handleShipOrder(orderId: string) {
    shipOrder.mutate(orderId, {
      onSuccess: () => toast.success("Shipment created via Shiprocket."),
      onError: (error) => toast.error(getApiErrorMessage(error)),
    })
  }

  function handleBulkShip() {
    bulkShip.mutate(Array.from(selectedIds), {
      onSuccess: (result) => {
        if (result.failed_count === 0) {
          toast.success(`${result.shipped_count} shipments created via Shiprocket.`)
        } else {
          toast.warning(
            `${result.shipped_count} shipped, ${result.failed_count} could not be shipped.`
          )
        }
        setBulkShipOpen(false)
        setSelectedIds(new Set())
      },
      onError: (error) => toast.error(getApiErrorMessage(error)),
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
      id: "sku_summary",
      header: "SKU",
      cell: (r) => <span className="block max-w-[160px] truncate">{r.sku_summary ?? "—"}</span>,
    },
    {
      id: "total_quantity",
      header: "Qty",
      className: "text-right",
      cell: (r) => r.total_quantity,
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
      id: "order_status",
      header: "Order Status",
      cell: () => <StatusBadge domain="order" status="confirmed" />,
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
            onClick={(e) => {
              e.stopPropagation()
              router.push(`/shipments/${r.shipment_id}`)
            }}
          >
            Process Shipment
          </Button>
        ) : (
          <Button
            size="sm"
            variant="outline"
            disabled={shipOrder.isPending && shipOrder.variables === r.order_id}
            onClick={(e) => {
              e.stopPropagation()
              handleShipOrder(r.order_id)
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
        title="Confirmed Orders — Shipment Queue"
        description={
          query.data
            ? `${query.data.meta.total_items} confirmed orders awaiting shipment processing.`
            : "Confirmed orders waiting for shipment processing."
        }
        actions={
          selectedIds.size > 0 ? (
            <Button size="sm" onClick={() => setBulkShipOpen(true)}>
              <Truck />
              Bulk Ship via Shiprocket ({selectedIds.size})
            </Button>
          ) : undefined
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
            <span className="font-medium">{selectedIds.size} orders selected</span>
            <Button variant="ghost" size="sm" onClick={() => setSelectedIds(new Set())}>
              Clear selection
            </Button>
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
            </>
          )}
        </QueryStates>
      </div>

      <AlertDialog open={bulkShipOpen} onOpenChange={setBulkShipOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              Ship {selectedIds.size} selected orders via Shiprocket?
            </AlertDialogTitle>
            <AlertDialogDescription>
              Creates a Shiprocket shipment for each order (checking stock availability first).
              Orders with insufficient stock, or that already have a shipment, are reported as
              failed without blocking the rest. You still assign AWB and request pickup per
              shipment afterward.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <Button onClick={handleBulkShip} disabled={bulkShip.isPending}>
              {bulkShip.isPending ? "Shipping..." : "Ship Orders"}
            </Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  )
}

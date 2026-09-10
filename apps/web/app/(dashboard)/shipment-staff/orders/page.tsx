"use client"

import * as React from "react"
import { ExternalLink } from "lucide-react"
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
import { copyToClipboard, SHIPROCKET_READY_TO_SHIP_URL } from "@/lib/shiprocket"
import { useUrlFilters } from "@/lib/use-url-filters"
import { useLocateShiprocketOrdersForMyScope, useMyConfirmedOrders } from "@/services/shipment-staff"
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
  const { filters, setFilters, clearFilters } = useUrlFilters(FILTER_DEFAULTS)
  const locate = useLocateShiprocketOrdersForMyScope()
  // Debounces a double-click into one open, not two tabs -- opening
  // Shiprocket's page is a read-only `window.open` (+ a clipboard
  // write), never a mutation, so this is purely a UX guard. Keyed per
  // order id so opening one row's link never disables another's button.
  const [openingOrderId, setOpeningOrderId] = React.useState<string | null>(null)

  function showUnavailable(message?: string | null) {
    toast.error("Shiprocket order ID is unavailable for this order.", {
      description:
        message ??
        "The OMS doesn't have a stored Shiprocket order id for this order yet -- it hasn't " +
          "been pushed to Shiprocket from here, and hasn't been matched back from Shiprocket " +
          "either.",
    })
  }

  // Opens Shiprocket's plain "Ready to Ship" page and copies `id` to the
  // clipboard so the operator can paste it into Shiprocket's own
  // "Multiple Order IDs" filter.
  async function openAndCopy(id: string) {
    window.open(SHIPROCKET_READY_TO_SHIP_URL, "_blank", "noopener,noreferrer")
    const copied = await copyToClipboard(id)
    if (copied) {
      toast.success(`Shiprocket Order ID ${id} copied.`, {
        description: "Paste it into Shiprocket's Multiple Order IDs filter to find this order.",
      })
    } else {
      toast.warning(`Shiprocket Order ID: ${id}`, {
        description:
          "Couldn't copy automatically -- copy this ID and paste it into Shiprocket's " +
          "Multiple Order IDs filter to find this order.",
      })
    }
  }

  function openShiprocketOrder(orderId: string, id: string | null) {
    if (openingOrderId === orderId || locate.isPending) return
    if (id) {
      setOpeningOrderId(orderId)
      void openAndCopy(id).finally(() => {
        window.setTimeout(
          () => setOpeningOrderId((current) => (current === orderId ? null : current)),
          1000
        )
      })
      return
    }
    // No locally-known Shiprocket order id yet -- ask the backend to
    // locate the EXISTING order live before giving up (never creates
    // one; see `useLocateShiprocketOrdersForMyScope`).
    setOpeningOrderId(orderId)
    locate.mutate([orderId], {
      onSuccess: (results) => {
        setOpeningOrderId(null)
        const result = results[0]
        if (result?.shiprocket_order_id) {
          void openAndCopy(result.shiprocket_order_id)
        } else {
          showUnavailable(result?.message)
        }
      },
      onError: (error) => {
        setOpeningOrderId(null)
        toast.error(getApiErrorMessage(error))
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
      // "Ship Order"/"Process Shipment" open Shiprocket's plain "Ready
      // to Ship" page in a new tab and copy the real Shiprocket order id
      // to the clipboard -- never a Shiprocket create-shipment API call
      // from here (Shiprocket may already have this order, e.g. via its
      // own Shopify channel connector, independent of this OMS, so
      // creating one here risked a real duplicate-shipment bug). See
      // `ShipmentActionCell` (Fulfillment's equivalent table) for the
      // identical rule -- kept as a small local duplicate rather than
      // reusing that component directly, since it also renders a "View"
      // button pointed at `/orders/{id}`, a page this Shipment Staff
      // role has no permission to open.
      cell: (r) =>
        r.shipment_id ? (
          <Button
            size="sm"
            variant="outline"
            disabled={openingOrderId === r.order_id}
            onClick={() => openShiprocketOrder(r.order_id, r.shiprocket_order_id)}
          >
            <ExternalLink className="size-3.5" />
            {openingOrderId === r.order_id && locate.isPending ? "Checking..." : "Process Shipment"}
          </Button>
        ) : (
          <Button
            size="sm"
            disabled={openingOrderId === r.order_id}
            onClick={() => openShiprocketOrder(r.order_id, r.shiprocket_order_id)}
          >
            <ExternalLink className="size-3.5" />
            {openingOrderId === r.order_id && locate.isPending ? "Checking..." : "Ship Order"}
          </Button>
        ),
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
    </>
  )
}

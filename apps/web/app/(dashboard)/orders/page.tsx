"use client"

import * as React from "react"
import { useRouter } from "next/navigation"
import { Columns3, Download } from "lucide-react"
import { toast } from "sonner"

import { DataTable, type DataTableColumn } from "@/components/shared/data-table"
import { DateRangePicker, type DateRangeValue } from "@/components/shared/date-range-picker"
import { FilterBar } from "@/components/shared/filter-bar"
import { PageHeader } from "@/components/shared/page-header"
import { PaginationBar } from "@/components/shared/pagination-bar"
import { QueryStates } from "@/components/shared/query-states"
import { StatusBadge } from "@/components/shared/status-badge"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Input } from "@/components/ui/input"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { getApiErrorMessage } from "@/lib/api-client"
import { formatDate, formatMoney } from "@/lib/format"
import { useLocalStorageState } from "@/lib/use-local-storage-state"
import { useUrlFilters } from "@/lib/use-url-filters"
import { useCouriers } from "@/services/couriers"
import { useExportOrders, useOrders } from "@/services/orders"
import {
  ORDER_STATUS_OPTIONS,
  PAYMENT_STATUS_OPTIONS,
  PAYMENT_TYPE_OPTIONS,
  type Order,
  type OrderStatus,
  type PaymentStatus,
  type PaymentType,
} from "@/types/order"
import { SHIPMENT_STATUS_OPTIONS } from "@/types/shipment"

const FILTER_DEFAULTS = {
  q: "",
  status: "",
  payment_status: "",
  payment_type: "",
  shipment_status: "",
  courier_id: "",
  sku: "",
  amount_min: "",
  amount_max: "",
  date_from: "",
  date_to: "",
  sort_by: "",
  sort_order: "",
  page: 1,
  page_size: 20,
}

interface ColumnDef {
  id: string
  label: string
  defaultVisible: boolean
  column: DataTableColumn<Order>
}

const ALL_COLUMNS: ColumnDef[] = [
  {
    id: "order_number",
    label: "Order ID",
    defaultVisible: true,
    column: {
      id: "order_number",
      header: "Order ID",
      sortKey: "order_number",
      cell: (order) => <span className="font-medium">{order.order_number}</span>,
    },
  },
  {
    id: "order_datetime",
    label: "Order Date",
    defaultVisible: true,
    column: {
      id: "order_datetime",
      header: "Order Date",
      sortKey: "order_datetime",
      cell: (o) => formatDate(o.order_datetime),
    },
  },
  {
    id: "customer_name",
    label: "Customer",
    defaultVisible: true,
    column: {
      id: "customer_name",
      header: "Customer",
      cell: (o) => o.customer_name ?? "—",
    },
  },
  {
    id: "customer_phone",
    label: "Phone",
    defaultVisible: false,
    column: { id: "customer_phone", header: "Phone", cell: (o) => o.customer_phone ?? "—" },
  },
  {
    id: "item_summary",
    label: "Product",
    defaultVisible: true,
    column: {
      id: "item_summary",
      header: "Product",
      cell: (o) => <span className="max-w-[220px] truncate">{o.item_summary ?? "—"}</span>,
    },
  },
  {
    id: "total_quantity",
    label: "Quantity",
    defaultVisible: false,
    column: {
      id: "total_quantity",
      header: "Qty",
      className: "text-right",
      cell: (o) => o.total_quantity ?? "—",
    },
  },
  {
    id: "total_amount",
    label: "Amount",
    defaultVisible: true,
    column: {
      id: "total_amount",
      header: "Amount",
      className: "text-right",
      sortKey: "total_amount",
      cell: (o) => formatMoney(o.total_amount, o.currency),
    },
  },
  {
    id: "payment_type",
    label: "Payment Type",
    defaultVisible: false,
    column: {
      id: "payment_type",
      header: "Payment Type",
      cell: (o) => o.payment_type.toUpperCase(),
    },
  },
  {
    id: "payment_status",
    label: "Payment Status",
    defaultVisible: true,
    column: {
      id: "payment_status",
      header: "Payment Status",
      cell: (o) => <StatusBadge domain="payment" status={o.payment_status} />,
    },
  },
  {
    id: "status",
    label: "Order Status",
    defaultVisible: true,
    column: {
      id: "status",
      header: "Order Status",
      cell: (o) => <StatusBadge domain="order" status={o.status} />,
    },
  },
  {
    id: "shipment_status",
    label: "Shipment Status",
    defaultVisible: true,
    column: {
      id: "shipment_status",
      header: "Shipment Status",
      cell: (o) => (o.shipment_status ? <StatusBadge domain="shipment" status={o.shipment_status} /> : "—"),
    },
  },
  {
    id: "courier_name",
    label: "Courier",
    defaultVisible: false,
    column: { id: "courier_name", header: "Courier", cell: (o) => o.courier_name ?? "—" },
  },
  {
    id: "tracking_number",
    label: "Tracking Number",
    defaultVisible: false,
    column: {
      id: "tracking_number",
      header: "Tracking Number",
      cell: (o) => <span className="font-mono text-xs">{o.tracking_number ?? "—"}</span>,
    },
  },
  {
    id: "created_at",
    label: "Created At",
    defaultVisible: false,
    column: {
      id: "created_at",
      header: "Created At",
      sortKey: "created_at",
      cell: (o) => formatDate(o.created_at),
    },
  },
]

const COLUMN_STORAGE_KEY = "oms_orders_visible_columns"
const DEFAULT_VISIBLE_COLUMN_IDS = ALL_COLUMNS.filter((c) => c.defaultVisible).map((c) => c.id)

function useVisibleColumns(): [Set<string>, (id: string) => void] {
  const [visibleIds, setVisibleIds] = useLocalStorageState<string[]>(
    COLUMN_STORAGE_KEY,
    DEFAULT_VISIBLE_COLUMN_IDS
  )
  const visible = React.useMemo(() => new Set(visibleIds), [visibleIds])

  const toggle = React.useCallback(
    (id: string) => {
      const next = new Set(visibleIds)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      setVisibleIds([...next])
    },
    [visibleIds, setVisibleIds]
  )

  return [visible, toggle]
}

/** Debounces free-text filter inputs (search/SKU/amount) so every
 * keystroke doesn't trigger a server round trip (spec §46).
 */
function useDebouncedValue<T>(value: T, delayMs: number): T {
  const [debounced, setDebounced] = React.useState(value)
  React.useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delayMs)
    return () => clearTimeout(timer)
  }, [value, delayMs])
  return debounced
}

function OrdersSkeleton() {
  return (
    <>
      <PageHeader title="Orders" description="Search, filter, and drill into any order." />
      <div className="flex flex-col gap-3">
        <Skeleton className="h-10 w-full max-w-2xl" />
        {Array.from({ length: 6 }).map((_, i) => (
          <Skeleton key={i} className="h-10 w-full" />
        ))}
      </div>
    </>
  )
}

export default function OrdersPage() {
  return (
    <React.Suspense fallback={<OrdersSkeleton />}>
      <OrdersPageContent />
    </React.Suspense>
  )
}

function OrdersPageContent() {
  const router = useRouter()
  const { filters, setFilters, clearFilters, queryString } = useUrlFilters(FILTER_DEFAULTS)
  const [visibleColumns, toggleColumn] = useVisibleColumns()
  const couriersQuery = useCouriers()
  const exportMutation = useExportOrders()

  const [searchInput, setSearchInput] = React.useState(filters.q)
  const [skuInput, setSkuInput] = React.useState(filters.sku)
  const [amountMinInput, setAmountMinInput] = React.useState(filters.amount_min)
  const [amountMaxInput, setAmountMaxInput] = React.useState(filters.amount_max)
  const debouncedSearch = useDebouncedValue(searchInput, 400)
  const debouncedSku = useDebouncedValue(skuInput, 400)
  const debouncedAmountMin = useDebouncedValue(amountMinInput, 400)
  const debouncedAmountMax = useDebouncedValue(amountMaxInput, 400)

  React.useEffect(() => {
    setFilters({ q: debouncedSearch })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debouncedSearch])
  React.useEffect(() => {
    setFilters({ sku: debouncedSku })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debouncedSku])
  React.useEffect(() => {
    setFilters({ amount_min: debouncedAmountMin })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debouncedAmountMin])
  React.useEffect(() => {
    setFilters({ amount_max: debouncedAmountMax })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debouncedAmountMax])

  const dateRange: DateRangeValue = {
    from: filters.date_from ? new Date(filters.date_from) : undefined,
    to: filters.date_to ? new Date(filters.date_to) : undefined,
  }

  const activeFilters = {
    q: filters.q || undefined,
    status: (filters.status || undefined) as OrderStatus | undefined,
    payment_status: (filters.payment_status || undefined) as PaymentStatus | undefined,
    payment_type: (filters.payment_type || undefined) as PaymentType | undefined,
    shipment_status: filters.shipment_status || undefined,
    courier_id: filters.courier_id || undefined,
    sku: filters.sku || undefined,
    amount_min: filters.amount_min || undefined,
    amount_max: filters.amount_max || undefined,
    date_from: filters.date_from || undefined,
    date_to: filters.date_to || undefined,
  }

  const query = useOrders({
    page: filters.page,
    pageSize: filters.page_size,
    sortBy: filters.sort_by || undefined,
    sortOrder: (filters.sort_order || undefined) as "asc" | "desc" | undefined,
    ...activeFilters,
  })

  function handleSortChange(sortKey: string) {
    if (filters.sort_by !== sortKey) {
      setFilters({ sort_by: sortKey, sort_order: "desc" })
    } else {
      setFilters({ sort_order: filters.sort_order === "asc" ? "desc" : "asc" })
    }
  }

  const columns = ALL_COLUMNS.filter((c) => visibleColumns.has(c.id)).map((c) => c.column)

  function handleClear() {
    setSearchInput("")
    setSkuInput("")
    setAmountMinInput("")
    setAmountMaxInput("")
    clearFilters()
  }

  function handleExport() {
    exportMutation.mutate(activeFilters, {
      onError: (error) => toast.error(getApiErrorMessage(error)),
    })
  }

  return (
    <>
      <PageHeader
        title="Orders"
        description={
          query.data ? `${query.data.meta.total_items} orders match the current filters.` : "Search, filter, and drill into any order."
        }
      />
      <div className="flex flex-col gap-3">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <FilterBar
            searchValue={searchInput}
            onSearchChange={setSearchInput}
            searchPlaceholder="Search by order #, customer, phone, email, tracking..."
            statusValue={filters.status || undefined}
            onStatusChange={(value) => setFilters({ status: value ?? "" })}
            statusOptions={ORDER_STATUS_OPTIONS}
            statusLabel="Order Status"
            extra={
              <DateRangePicker
                value={dateRange}
                onChange={(range) =>
                  setFilters({
                    date_from: range.from ? range.from.toISOString() : "",
                    date_to: range.to ? range.to.toISOString() : "",
                  })
                }
              />
            }
          />
          <div className="flex items-center gap-2">
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button variant="outline" size="sm">
                  <Columns3 className="size-4" />
                  Columns
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="w-52">
                <DropdownMenuLabel>Visible columns</DropdownMenuLabel>
                <DropdownMenuSeparator />
                {ALL_COLUMNS.map((c) => (
                  <DropdownMenuCheckboxItem
                    key={c.id}
                    checked={visibleColumns.has(c.id)}
                    onCheckedChange={() => toggleColumn(c.id)}
                    onSelect={(e) => e.preventDefault()}
                  >
                    {c.label}
                  </DropdownMenuCheckboxItem>
                ))}
              </DropdownMenuContent>
            </DropdownMenu>
            <Button
              variant="outline"
              size="sm"
              disabled={exportMutation.isPending}
              onClick={handleExport}
            >
              <Download className="size-4" />
              {exportMutation.isPending ? "Exporting..." : "Export"}
            </Button>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <Select
            value={filters.payment_type || "__all__"}
            onValueChange={(v) => setFilters({ payment_type: v === "__all__" ? "" : v })}
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
            value={filters.payment_status || "__all__"}
            onValueChange={(v) => setFilters({ payment_status: v === "__all__" ? "" : v })}
          >
            <SelectTrigger className="w-[170px]">
              <SelectValue placeholder="Payment Status" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="__all__">All payment statuses</SelectItem>
              {PAYMENT_STATUS_OPTIONS.map((o) => (
                <SelectItem key={o.value} value={o.value}>
                  {o.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>

          <Select
            value={filters.shipment_status || "__all__"}
            onValueChange={(v) => setFilters({ shipment_status: v === "__all__" ? "" : v })}
          >
            <SelectTrigger className="w-[170px]">
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
            value={filters.courier_id || "__all__"}
            onValueChange={(v) => setFilters({ courier_id: v === "__all__" ? "" : v })}
          >
            <SelectTrigger className="w-[160px]">
              <SelectValue placeholder="Courier" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="__all__">All couriers</SelectItem>
              {couriersQuery.data?.map((courier) => (
                <SelectItem key={courier.id} value={courier.id}>
                  {courier.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>

          <Input
            value={skuInput}
            onChange={(e) => setSkuInput(e.target.value)}
            placeholder="SKU / Product"
            className="w-[140px]"
          />
          <Input
            value={amountMinInput}
            onChange={(e) => setAmountMinInput(e.target.value)}
            placeholder="Min amount"
            type="number"
            className="w-[110px]"
          />
          <Input
            value={amountMaxInput}
            onChange={(e) => setAmountMaxInput(e.target.value)}
            placeholder="Max amount"
            type="number"
            className="w-[110px]"
          />

          <Button variant="ghost" size="sm" onClick={handleClear}>
            Clear filters
          </Button>
        </div>

        <QueryStates
          isLoading={query.isLoading}
          isError={query.isError}
          error={query.error}
          data={query.data}
          onRetry={() => void query.refetch()}
          isEmpty={(data) => data.data.length === 0}
          emptyTitle="No orders found"
          emptyDescription="Try adjusting your search or filters."
        >
          {(data) => (
            <>
              <DataTable
                columns={columns}
                data={data.data}
                rowKey={(order) => order.id}
                onRowClick={(order) =>
                  router.push(
                    `/orders/${order.id}${queryString ? `?from=${encodeURIComponent(queryString)}` : ""}`
                  )
                }
                sortBy={filters.sort_by || undefined}
                sortOrder={(filters.sort_order || undefined) as "asc" | "desc" | undefined}
                onSortChange={handleSortChange}
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
    </>
  )
}

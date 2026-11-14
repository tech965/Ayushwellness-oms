"use client"

import * as React from "react"
import { useRouter } from "next/navigation"
import { format, parseISO } from "date-fns"

import { DataTable, type DataTableColumn } from "@/components/shared/data-table"
import {
  DateRangePicker,
  type DateRangeValue,
} from "@/components/shared/date-range-picker"
import { FilterBar } from "@/components/shared/filter-bar"
import { PageHeader } from "@/components/shared/page-header"
import { PaginationBar } from "@/components/shared/pagination-bar"
import { QueryStates } from "@/components/shared/query-states"
import { StatusBadge } from "@/components/shared/status-badge"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Separator } from "@/components/ui/separator"
import { formatMoney } from "@/lib/format"
import { istCalendarDateAsLocalMidnight } from "@/lib/ist-date"
import { useUrlFilters } from "@/lib/use-url-filters"
import { useMarketplaceOrders } from "@/services/marketplace"
import {
  FULFILLMENT_STATUS_OPTIONS,
  ORDER_STATUS_OPTIONS,
  PAYMENT_STATUS_OPTIONS,
  type FulfillmentStatus,
  type OrderStatus,
  type PaymentStatus,
} from "@/types/order"
import {
  MARKETPLACE_OPTIONS,
  marketplaceLabel,
  type Marketplace,
  type MarketplaceOrder,
} from "@/types/marketplace"
import { SHIPMENT_STATUS_OPTIONS } from "@/types/shipment"

const FILTER_DEFAULTS = {
  q: "",
  marketplace: "",
  status: "",
  payment_status: "",
  fulfillment_status: "",
  shipment_status: "",
  sku: "",
  date_from: "",
  date_to: "",
  page: 1,
  page_size: 20,
}

/** Same IST-calendar-day rendering as the main Orders page's `order_date`
 * column (`app/(dashboard)/orders/page.tsx::formatIstDate`) — the date
 * filter below compares `order_datetime` against IST calendar days, so
 * the displayed date must agree with it regardless of the viewer's own
 * browser timezone.
 */
function formatIstDate(iso: string): string {
  return format(istCalendarDateAsLocalMidnight(parseISO(iso)), "d MMM yyyy")
}

function useDebouncedValue<T>(value: T, delayMs: number): T {
  const [debounced, setDebounced] = React.useState(value)
  React.useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delayMs)
    return () => clearTimeout(timer)
  }, [value, delayMs])
  return debounced
}

function buildColumns(router: ReturnType<typeof useRouter>): DataTableColumn<MarketplaceOrder>[] {
  return [
    {
      id: "source_system",
      header: "Marketplace",
      cell: (o) => <Badge variant="secondary">{marketplaceLabel(o.source_system)}</Badge>,
    },
    {
      id: "external_id",
      header: "Marketplace Order ID",
      cell: (o) => <span className="font-mono text-xs">{o.external_id ?? "—"}</span>,
    },
    {
      id: "order_datetime",
      header: "Order Date",
      sortKey: "order_datetime",
      cell: (o) => formatIstDate(o.order_datetime),
    },
    {
      id: "customer_name",
      header: "Customer Name",
      cell: (o) => o.customer_name ?? "—",
    },
    {
      id: "customer_phone",
      header: "Customer Phone",
      cell: (o) => o.customer_phone ?? "—",
    },
    {
      id: "item_summary",
      header: "Products",
      cell: (o) => <span className="max-w-[200px] truncate">{o.item_summary ?? "—"}</span>,
    },
    {
      id: "sku_summary",
      header: "SKU",
      cell: (o) => <span className="font-mono text-xs">{o.sku_summary ?? "—"}</span>,
    },
    {
      id: "total_quantity",
      header: "Quantity",
      className: "text-right",
      cell: (o) => o.total_quantity ?? "—",
    },
    {
      id: "total_amount",
      header: "Order Value",
      className: "text-right",
      sortKey: "total_amount",
      cell: (o) => formatMoney(o.total_amount, o.currency),
    },
    {
      id: "payment_type",
      header: "Payment Method",
      cell: (o) => o.payment_type.toUpperCase(),
    },
    {
      id: "payment_status",
      header: "Payment Status",
      cell: (o) => <StatusBadge domain="payment" status={o.payment_status} />,
    },
    {
      id: "fulfillment_status",
      header: "Fulfillment Status",
      cell: (o) => <StatusBadge domain="fulfillment" status={o.fulfillment_status} />,
    },
    {
      id: "shipment_status",
      header: "Shipment Status",
      cell: (o) =>
        o.shipment_status ? (
          <StatusBadge domain="shipment" status={o.shipment_status} />
        ) : (
          "—"
        ),
    },
    {
      id: "tracking_number",
      header: "Tracking ID",
      cell: (o) => <span className="font-mono text-xs">{o.tracking_number ?? "—"}</span>,
    },
    {
      id: "actions",
      header: "Actions",
      cell: (o) => (
        <Button
          variant="ghost"
          size="sm"
          onClick={(e) => {
            e.stopPropagation()
            router.push(`/orders/${o.id}`)
          }}
        >
          View
        </Button>
      ),
    },
  ]
}

export function MarketplaceOrdersView({ fixedMarketplace }: { fixedMarketplace?: Marketplace }) {
  const router = useRouter()
  const { filters, setFilters, clearFilters } = useUrlFilters(FILTER_DEFAULTS)

  const [searchInput, setSearchInput] = React.useState(filters.q)
  const [skuInput, setSkuInput] = React.useState(filters.sku)
  const debouncedSearch = useDebouncedValue(searchInput, 400)
  const debouncedSku = useDebouncedValue(skuInput, 400)

  React.useEffect(() => {
    setFilters({ q: debouncedSearch })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debouncedSearch])
  React.useEffect(() => {
    setFilters({ sku: debouncedSku })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debouncedSku])

  const dateRange: DateRangeValue = {
    from: filters.date_from ? new Date(filters.date_from) : undefined,
    to: filters.date_to ? new Date(filters.date_to) : undefined,
  }

  const query = useMarketplaceOrders({
    page: filters.page,
    pageSize: filters.page_size,
    marketplace: fixedMarketplace ?? ((filters.marketplace || undefined) as Marketplace | undefined),
    status: (filters.status || undefined) as OrderStatus | undefined,
    payment_status: (filters.payment_status || undefined) as PaymentStatus | undefined,
    fulfillment_status: (filters.fulfillment_status || undefined) as
      FulfillmentStatus | undefined,
    shipment_status: filters.shipment_status || undefined,
    sku: filters.sku || undefined,
    date_from: filters.date_from || undefined,
    date_to: filters.date_to || undefined,
    q: filters.q || undefined,
  })

  const columns = React.useMemo(() => buildColumns(router), [router])

  const title = fixedMarketplace
    ? `${marketplaceLabel(fixedMarketplace)} Orders`
    : "All Marketplace Orders"

  return (
    <>
      <PageHeader
        title={title}
        description="Orders ingested from Amazon, Flipkart, and Meesho — kept separate from Shopify Orders."
      />
      <div className="flex flex-col gap-4">
        <div className="bg-card border-border flex flex-col gap-3 rounded-lg border p-3">
          <FilterBar
            className="border-none p-0"
            searchValue={searchInput}
            onSearchChange={setSearchInput}
            searchPlaceholder="Search by order ID, customer name, phone, tracking..."
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
          <Separator />
          <div className="flex flex-wrap items-center gap-2">
            {!fixedMarketplace && (
              <Select
                value={filters.marketplace || "__all__"}
                onValueChange={(v) => setFilters({ marketplace: v === "__all__" ? "" : v })}
              >
                <SelectTrigger className="w-[160px]">
                  <SelectValue placeholder="Marketplace" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="__all__">All marketplaces</SelectItem>
                  {MARKETPLACE_OPTIONS.map((o) => (
                    <SelectItem key={o.value} value={o.value}>
                      {o.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            )}

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
              value={filters.fulfillment_status || "__all__"}
              onValueChange={(v) => setFilters({ fulfillment_status: v === "__all__" ? "" : v })}
            >
              <SelectTrigger className="w-[160px]">
                <SelectValue placeholder="Fulfillment" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="__all__">All fulfillment</SelectItem>
                {FULFILLMENT_STATUS_OPTIONS.map((o) => (
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

            <input
              value={skuInput}
              onChange={(e) => setSkuInput(e.target.value)}
              placeholder="SKU"
              className="border-input bg-background h-9 w-[140px] rounded-md border px-3 text-sm"
            />

            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                setSearchInput("")
                setSkuInput("")
                clearFilters()
              }}
            >
              Clear filters
            </Button>
          </div>
        </div>

        <QueryStates
          isLoading={query.isLoading}
          isError={query.isError}
          error={query.error}
          data={query.data}
          onRetry={() => void query.refetch()}
          isEmpty={(data) => data.data.length === 0}
          emptyTitle="No marketplace orders found"
          emptyDescription={
            fixedMarketplace
              ? `No ${marketplaceLabel(fixedMarketplace)} orders match these filters yet.`
              : "No Amazon/Flipkart/Meesho orders match these filters yet."
          }
        >
          {(data) => (
            <div
              className={
                query.isFetching && !query.isLoading
                  ? "pointer-events-none opacity-50 transition-opacity"
                  : undefined
              }
            >
              <DataTable
                columns={columns}
                data={data.data}
                rowKey={(order) => order.id}
                onRowClick={(order) => router.push(`/orders/${order.id}`)}
              />
              <PaginationBar
                meta={data.meta}
                onPageChange={(page) => setFilters({ page })}
                pageSizeOptions={[10, 20, 50, 100]}
                onPageSizeChange={(page_size) => setFilters({ page_size, page: 1 })}
              />
            </div>
          )}
        </QueryStates>
      </div>
    </>
  )
}

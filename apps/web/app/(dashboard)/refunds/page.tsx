"use client"

import * as React from "react"
import { useRouter } from "next/navigation"
import { CheckCircle2, Clock, ListChecks } from "lucide-react"

import { DataTable, type DataTableColumn } from "@/components/shared/data-table"
import {
  DateRangePicker,
  type DateRangeValue,
} from "@/components/shared/date-range-picker"
import { FilterBar } from "@/components/shared/filter-bar"
import { PageHeader } from "@/components/shared/page-header"
import { PaginationBar } from "@/components/shared/pagination-bar"
import { QueryStates } from "@/components/shared/query-states"
import { StatTile } from "@/components/shared/stat-tile"
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
import { useRefunds } from "@/services/refunds"
import { REFUND_STATUS_OPTIONS, type Refund, type RefundStatus } from "@/types/refund"
import { PAYMENT_TYPE_OPTIONS, type PaymentType } from "@/types/order"

const FILTER_DEFAULTS = {
  q: "",
  status: "",
  payment_type: "",
  date_from: "",
  date_to: "",
  page: 1,
  page_size: 20,
}

export default function RefundsPage() {
  const router = useRouter()
  const { filters, setFilters, clearFilters } = useUrlFilters(FILTER_DEFAULTS)

  const [searchInput, setSearchInput] = React.useState(filters.q)
  React.useEffect(() => {
    const timer = setTimeout(() => setFilters({ q: searchInput }), 400)
    return () => clearTimeout(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchInput])

  const dateRange: DateRangeValue = {
    from: filters.date_from ? new Date(filters.date_from) : undefined,
    to: filters.date_to ? new Date(filters.date_to) : undefined,
  }

  const activeFilters = {
    q: filters.q || undefined,
    status: (filters.status || undefined) as RefundStatus | undefined,
    payment_type: (filters.payment_type || undefined) as PaymentType | undefined,
    date_from: filters.date_from || undefined,
    date_to: filters.date_to || undefined,
  }

  function handleClear() {
    setSearchInput("")
    clearFilters()
  }

  const query = useRefunds({ page: filters.page, pageSize: filters.page_size, ...activeFilters })

  // KPIs reflect the currently active search/payment/date filters, but
  // never the status filter itself (each card IS a status bucket) — same
  // convention as the NDR/RTO/Returns pages.
  const kpiBase = {
    q: activeFilters.q,
    payment_type: activeFilters.payment_type,
    date_from: activeFilters.date_from,
    date_to: activeFilters.date_to,
  }
  const totalQuery = useRefunds({ page: 1, pageSize: 1, ...kpiBase })
  const completedQuery = useRefunds({ page: 1, pageSize: 1, ...kpiBase, status: "completed" })
  const total = totalQuery.data?.meta.total_items
  const completed = completedQuery.data?.meta.total_items
  const pending = total !== undefined && completed !== undefined ? total - completed : undefined

  const columns: DataTableColumn<Refund>[] = [
    {
      id: "order_number",
      header: "Order ID",
      cell: (refund) => <span className="font-medium">{refund.order_number ?? "—"}</span>,
    },
    { id: "customer", header: "Customer", cell: (refund) => refund.customer_name ?? "—" },
    { id: "phone", header: "Phone", cell: (refund) => refund.customer_phone ?? "—" },
    {
      id: "product",
      header: "Product",
      cell: (refund) => <span className="max-w-[200px] truncate">{refund.product ?? "—"}</span>,
    },
    {
      id: "order_amount",
      header: "Order Amount",
      className: "text-right",
      cell: (refund) => (refund.order_amount ? formatMoney(refund.order_amount) : "—"),
    },
    {
      id: "amount",
      header: "Refund Amount",
      className: "text-right",
      cell: (refund) => formatMoney(refund.amount),
    },
    {
      id: "payment_type",
      header: "Payment",
      cell: (refund) => refund.payment_type?.toUpperCase() ?? "—",
    },
    { id: "reason", header: "Reason", cell: (refund) => refund.reason ?? "—" },
    {
      id: "status",
      header: "Status",
      cell: (refund) => <StatusBadge domain="refund" status={refund.status} />,
    },
    {
      id: "initiated",
      header: "Initiated",
      cell: (refund) => (refund.initiated_at ? formatDate(refund.initiated_at) : "—"),
    },
    {
      id: "completed",
      header: "Completed",
      cell: (refund) => (refund.completed_at ? formatDate(refund.completed_at) : "—"),
    },
    { id: "created", header: "Created", cell: (refund) => formatDate(refund.created_at) },
  ]

  return (
    <>
      <PageHeader title="Refunds" description="Customer refunds and their progress." />
      <div className="flex flex-col gap-4">
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
          <StatTile label="Total Refunds" value={total ?? "—"} icon={ListChecks} accent="slate" />
          <StatTile
            label="Pending / Processing"
            value={pending ?? "—"}
            icon={Clock}
            accent="amber"
          />
          <StatTile
            label="Completed"
            value={completed ?? "—"}
            icon={CheckCircle2}
            accent="emerald"
          />
        </div>

        <FilterBar
          searchValue={searchInput}
          onSearchChange={setSearchInput}
          searchPlaceholder="Search by order #, customer, phone, product..."
          statusValue={filters.status || undefined}
          onStatusChange={(value) => setFilters({ status: value ?? "" })}
          statusOptions={REFUND_STATUS_OPTIONS}
          statusLabel="Status"
          extra={
            <>
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
              <DateRangePicker
                value={dateRange}
                onChange={(range) =>
                  setFilters({
                    date_from: range.from ? range.from.toISOString() : "",
                    date_to: range.to ? range.to.toISOString() : "",
                  })
                }
              />
            </>
          }
        />

        <QueryStates
          isLoading={query.isLoading}
          isError={query.isError}
          error={query.error}
          data={query.data}
          onRetry={() => void query.refetch()}
          isEmpty={(data) => data.data.length === 0}
          emptyTitle="No refunds found"
          emptyDescription="Refunds appear here automatically once a return is marked completed."
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
                rowKey={(refund) => refund.id}
                onRowClick={(refund) => router.push(`/orders/${refund.order_id}`)}
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
        {(filters.q ||
          filters.status ||
          filters.payment_type ||
          filters.date_from ||
          filters.date_to) && (
          <Button variant="ghost" size="sm" className="w-fit" onClick={handleClear}>
            Clear all filters
          </Button>
        )}
      </div>
    </>
  )
}

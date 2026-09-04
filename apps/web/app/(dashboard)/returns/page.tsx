"use client"

import * as React from "react"
import { useRouter } from "next/navigation"
import { CheckCircle2, ListChecks, RotateCcw } from "lucide-react"
import { toast } from "sonner"

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
import { Skeleton } from "@/components/ui/skeleton"
import { getApiErrorMessage } from "@/lib/api-client"
import { useAuth } from "@/lib/auth-context"
import { formatDate, formatMoney } from "@/lib/format"
import { useUrlFilters } from "@/lib/use-url-filters"
import { useReturns, useUpdateReturn } from "@/services/returns"
import { RETURN_STATUS_OPTIONS, type Return, type ReturnStatus } from "@/types/return"
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

function ReturnStatusCell({ ret }: { ret: Return }) {
  const { hasPermission } = useAuth()
  const update = useUpdateReturn(ret.id)

  if (!hasPermission("returns.update")) {
    return <StatusBadge domain="return" status={ret.status} />
  }

  return (
    <div onClick={(e) => e.stopPropagation()}>
      <Select
        value={ret.status}
        onValueChange={(value) => {
          update.mutate(
            { status: value as ReturnStatus },
            {
              onSuccess: () => toast.success("Return updated."),
              onError: (error) => toast.error(getApiErrorMessage(error)),
            }
          )
        }}
      >
        <SelectTrigger size="sm" className="w-[160px]">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {RETURN_STATUS_OPTIONS.map((option) => (
            <SelectItem key={option.value} value={option.value}>
              {option.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  )
}

function ReturnsSkeleton() {
  return (
    <>
      <PageHeader title="Returns" description="Product return requests and their progress." />
      <div className="flex flex-col gap-3">
        <Skeleton className="h-10 w-full max-w-2xl" />
        {Array.from({ length: 6 }).map((_, i) => (
          <Skeleton key={i} className="h-10 w-full" />
        ))}
      </div>
    </>
  )
}

export default function ReturnsPage() {
  return (
    <React.Suspense fallback={<ReturnsSkeleton />}>
      <ReturnsPageContent />
    </React.Suspense>
  )
}

function ReturnsPageContent() {
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
    status: (filters.status || undefined) as ReturnStatus | undefined,
    payment_type: (filters.payment_type || undefined) as PaymentType | undefined,
    date_from: filters.date_from || undefined,
    date_to: filters.date_to || undefined,
  }

  function handleClear() {
    setSearchInput("")
    clearFilters()
  }

  const query = useReturns({ page: filters.page, pageSize: filters.page_size, ...activeFilters })

  // KPIs reflect the currently active search/payment/date filters, but
  // never the status filter itself (each card IS a status bucket) — same
  // convention as the NDR/RTO pages.
  const kpiBase = {
    q: activeFilters.q,
    payment_type: activeFilters.payment_type,
    date_from: activeFilters.date_from,
    date_to: activeFilters.date_to,
  }
  const totalQuery = useReturns({ page: 1, pageSize: 1, ...kpiBase })
  const completedQuery = useReturns({ page: 1, pageSize: 1, ...kpiBase, status: "completed" })
  const total = totalQuery.data?.meta.total_items
  const completed = completedQuery.data?.meta.total_items
  const pending = total !== undefined && completed !== undefined ? total - completed : undefined

  const columns: DataTableColumn<Return>[] = [
    {
      id: "order_number",
      header: "Order ID",
      cell: (ret) => <span className="font-medium">{ret.order_number ?? "—"}</span>,
    },
    { id: "customer", header: "Customer", cell: (ret) => ret.customer_name ?? "—" },
    { id: "phone", header: "Phone", cell: (ret) => ret.customer_phone ?? "—" },
    {
      id: "product",
      header: "Product",
      cell: (ret) => <span className="max-w-[200px] truncate">{ret.product ?? "—"}</span>,
    },
    { id: "quantity", header: "Qty", className: "text-right", cell: (ret) => ret.quantity },
    {
      id: "amount",
      header: "Order Amount",
      className: "text-right",
      cell: (ret) => (ret.order_amount ? formatMoney(ret.order_amount) : "—"),
    },
    {
      id: "payment_type",
      header: "Payment",
      cell: (ret) => ret.payment_type?.toUpperCase() ?? "—",
    },
    { id: "reason", header: "Reason", cell: (ret) => ret.reason ?? "—" },
    { id: "status", header: "Status", cell: (ret) => <ReturnStatusCell ret={ret} /> },
    {
      id: "requested",
      header: "Requested",
      cell: (ret) => (ret.requested_at ? formatDate(ret.requested_at) : "—"),
    },
    {
      id: "approved",
      header: "Approved",
      cell: (ret) => (ret.approved_at ? formatDate(ret.approved_at) : "—"),
    },
    {
      id: "received",
      header: "Received",
      cell: (ret) => (ret.received_at ? formatDate(ret.received_at) : "—"),
    },
    {
      id: "completed",
      header: "Completed",
      cell: (ret) => (ret.completed_at ? formatDate(ret.completed_at) : "—"),
    },
  ]

  return (
    <>
      <PageHeader title="Returns" description="Product return requests and their progress." />
      <div className="flex flex-col gap-4">
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
          <StatTile label="Total Returns" value={total ?? "—"} icon={ListChecks} accent="slate" />
          <StatTile
            label="Requested / Pending"
            value={pending ?? "—"}
            icon={RotateCcw}
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
          statusOptions={RETURN_STATUS_OPTIONS}
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
          emptyTitle="No returns found"
          emptyDescription="Return requests appear here once a customer or operator initiates one."
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
                rowKey={(ret) => ret.id}
                onRowClick={(ret) => router.push(`/orders/${ret.order_id}`)}
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

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
import { getApiErrorMessage } from "@/lib/api-client"
import { useAuth } from "@/lib/auth-context"
import { formatDate, formatMoney } from "@/lib/format"
import { useUrlFilters } from "@/lib/use-url-filters"
import { useRtos, useUpdateRto } from "@/services/rto"
import { RTO_STATUS_OPTIONS, type RTO, type RTOStatus } from "@/types/rto"
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

function RtoStatusCell({ rto }: { rto: RTO }) {
  const { hasPermission } = useAuth()
  const update = useUpdateRto(rto.id)

  if (!hasPermission("rto.update")) {
    return <StatusBadge domain="rto" status={rto.status} />
  }

  return (
    <div onClick={(e) => e.stopPropagation()}>
      <Select
        value={rto.status}
        onValueChange={(value) => {
          update.mutate(
            { status: value as RTOStatus },
            {
              onSuccess: () => toast.success("RTO updated."),
              onError: (error) => toast.error(getApiErrorMessage(error)),
            }
          )
        }}
      >
        <SelectTrigger size="sm" className="w-[170px]">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {RTO_STATUS_OPTIONS.map((option) => (
            <SelectItem key={option.value} value={option.value}>
              {option.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  )
}

export default function RtoPage() {
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
    status: (filters.status || undefined) as RTOStatus | undefined,
    payment_type: (filters.payment_type || undefined) as PaymentType | undefined,
    date_from: filters.date_from || undefined,
    date_to: filters.date_to || undefined,
  }

  function handleClear() {
    setSearchInput("")
    clearFilters()
  }

  const query = useRtos({
    page: filters.page,
    pageSize: filters.page_size,
    ...activeFilters,
  })

  // KPIs reflect the currently active search/payment/date filters, but
  // never the status filter itself (each card IS a status bucket) — same
  // convention as the NDR page.
  const kpiBase = {
    q: activeFilters.q,
    payment_type: activeFilters.payment_type,
    date_from: activeFilters.date_from,
    date_to: activeFilters.date_to,
  }
  const totalQuery = useRtos({ page: 1, pageSize: 1, ...kpiBase })
  const receivedQuery = useRtos({ page: 1, pageSize: 1, ...kpiBase, status: "received" })
  const total = totalQuery.data?.meta.total_items
  const received = receivedQuery.data?.meta.total_items
  const inProgress =
    total !== undefined && received !== undefined ? total - received : undefined

  const columns: DataTableColumn<RTO>[] = [
    {
      id: "order_number",
      header: "Order ID",
      cell: (rto) => <span className="font-medium">{rto.order_number ?? "—"}</span>,
    },
    { id: "customer", header: "Customer", cell: (rto) => rto.customer_name ?? "—" },
    { id: "phone", header: "Phone", cell: (rto) => rto.customer_phone ?? "—" },
    {
      id: "product",
      header: "Product",
      cell: (rto) => <span className="max-w-[200px] truncate">{rto.product ?? "—"}</span>,
    },
    {
      id: "amount",
      header: "Amount",
      className: "text-right",
      cell: (rto) => (rto.order_amount ? formatMoney(rto.order_amount) : "—"),
    },
    {
      id: "payment_type",
      header: "Payment",
      cell: (rto) => rto.payment_type?.toUpperCase() ?? "—",
    },
    {
      id: "awb",
      header: "AWB",
      cell: (rto) => <span className="font-mono text-xs">{rto.awb ?? "—"}</span>,
    },
    { id: "courier", header: "Courier", cell: (rto) => rto.courier_name ?? "—" },
    {
      id: "reason",
      header: "Reason",
      cell: (rto) => rto.reason ?? rto.external_reason ?? "—",
    },
    { id: "status", header: "Status", cell: (rto) => <RtoStatusCell rto={rto} /> },
    {
      id: "shipment_status",
      header: "Shipment status",
      cell: (rto) =>
        rto.shipment_status ? (
          <StatusBadge domain="shipment" status={rto.shipment_status} />
        ) : (
          "—"
        ),
    },
    {
      id: "initiated",
      header: "Initiated",
      cell: (rto) => (rto.initiated_at ? formatDate(rto.initiated_at) : "—"),
    },
    {
      id: "completed",
      header: "Completed",
      cell: (rto) => (rto.completed_at ? formatDate(rto.completed_at) : "—"),
    },
    { id: "created", header: "Created", cell: (rto) => formatDate(rto.created_at) },
  ]

  return (
    <>
      <PageHeader title="RTO" description="Return-to-origin shipments." />
      <div className="flex flex-col gap-4">
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
          <StatTile
            label="Total RTO"
            value={total ?? "—"}
            icon={ListChecks}
            accent="slate"
          />
          <StatTile
            label="Initiated / In Progress"
            value={inProgress ?? "—"}
            icon={RotateCcw}
            accent="amber"
          />
          <StatTile
            label="Completed / Received"
            value={received ?? "—"}
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
          statusOptions={RTO_STATUS_OPTIONS}
          statusLabel="Status"
          extra={
            <>
              <Select
                value={filters.payment_type || "__all__"}
                onValueChange={(v) =>
                  setFilters({ payment_type: v === "__all__" ? "" : v })
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
          emptyTitle="No RTO records"
          emptyDescription="RTO records are derived automatically from Shiprocket tracking refreshes."
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
                rowKey={(rto) => rto.id}
                onRowClick={(rto) => router.push(`/orders/${rto.order_id}`)}
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

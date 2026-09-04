"use client"

import * as React from "react"
import { useRouter } from "next/navigation"
import { AlertTriangle, CheckCircle2, ListChecks } from "lucide-react"
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
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
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
import { useNdrReattempt, useNdrs, useUpdateNdr } from "@/services/ndr"
import { NDR_STATUS_OPTIONS, type NDR, type NDRStatus } from "@/types/ndr"
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

function NdrStatusCell({ ndr }: { ndr: NDR }) {
  const { hasPermission } = useAuth()
  const update = useUpdateNdr(ndr.id)

  if (!hasPermission("ndr.update")) {
    return <StatusBadge domain="ndr" status={ndr.status} />
  }

  return (
    <div onClick={(e) => e.stopPropagation()}>
      <Select
        value={ndr.status}
        onValueChange={(value) => {
          update.mutate(
            { status: value as NDRStatus },
            {
              onSuccess: () => toast.success("NDR updated."),
              onError: (error) => toast.error(getApiErrorMessage(error)),
            }
          )
        }}
      >
        <SelectTrigger size="sm" className="w-[190px]">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {NDR_STATUS_OPTIONS.map((option) => (
            <SelectItem key={option.value} value={option.value}>
              {option.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  )
}

function NdrReattemptAction({ ndr }: { ndr: NDR }) {
  const { hasPermission } = useAuth()
  const [open, setOpen] = React.useState(false)
  const [address1, setAddress1] = React.useState("")
  const [address2, setAddress2] = React.useState("")
  const [phone, setPhone] = React.useState("")
  const reattempt = useNdrReattempt(ndr.id)

  if (!hasPermission("ndr.update")) return null

  function submit() {
    reattempt.mutate(
      { address_1: address1, address_2: address2 || undefined, phone },
      {
        onSuccess: () => {
          toast.success("Reattempt requested via Shiprocket.")
          setOpen(false)
        },
        onError: (error) => toast.error(getApiErrorMessage(error)),
      }
    )
  }

  return (
    <div onClick={(e) => e.stopPropagation()}>
      <Dialog open={open} onOpenChange={setOpen}>
        <Button variant="outline" size="sm" onClick={() => setOpen(true)}>
          Reattempt
        </Button>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Request delivery reattempt</DialogTitle>
          </DialogHeader>
          <div className="flex flex-col gap-3">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="address1">Address line 1</Label>
              <Input
                id="address1"
                value={address1}
                onChange={(e) => setAddress1(e.target.value)}
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="address2">Address line 2 (optional)</Label>
              <Input
                id="address2"
                value={address2}
                onChange={(e) => setAddress2(e.target.value)}
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="phone">Phone</Label>
              <Input
                id="phone"
                value={phone}
                onChange={(e) => setPhone(e.target.value)}
              />
            </div>
          </div>
          <DialogFooter>
            <Button
              disabled={!address1 || !phone || reattempt.isPending}
              onClick={submit}
            >
              {reattempt.isPending ? "Requesting..." : "Request reattempt"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}

export default function NdrPage() {
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
    status: (filters.status || undefined) as NDRStatus | undefined,
    payment_type: (filters.payment_type || undefined) as PaymentType | undefined,
    date_from: filters.date_from || undefined,
    date_to: filters.date_to || undefined,
  }

  function handleClear() {
    setSearchInput("")
    clearFilters()
  }

  const query = useNdrs({
    page: filters.page,
    pageSize: filters.page_size,
    ...activeFilters,
  })

  // KPIs reflect the currently active search/payment/date filters, but
  // never the status filter itself (each card IS a status bucket) — two
  // lightweight page_size=1 calls reusing the same list endpoint, "Open"
  // derived by subtraction rather than requiring a multi-status backend
  // filter that doesn't exist.
  const kpiBase = {
    q: activeFilters.q,
    payment_type: activeFilters.payment_type,
    date_from: activeFilters.date_from,
    date_to: activeFilters.date_to,
  }
  const totalQuery = useNdrs({ page: 1, pageSize: 1, ...kpiBase })
  const resolvedQuery = useNdrs({ page: 1, pageSize: 1, ...kpiBase, status: "resolved" })
  const total = totalQuery.data?.meta.total_items
  const resolved = resolvedQuery.data?.meta.total_items
  const open =
    total !== undefined && resolved !== undefined ? total - resolved : undefined

  const columns: DataTableColumn<NDR>[] = [
    {
      id: "order_number",
      header: "Order ID",
      cell: (ndr) => <span className="font-medium">{ndr.order_number ?? "—"}</span>,
    },
    { id: "customer", header: "Customer", cell: (ndr) => ndr.customer_name ?? "—" },
    { id: "phone", header: "Phone", cell: (ndr) => ndr.customer_phone ?? "—" },
    {
      id: "product",
      header: "Product",
      cell: (ndr) => <span className="max-w-[200px] truncate">{ndr.product ?? "—"}</span>,
    },
    {
      id: "amount",
      header: "Amount",
      className: "text-right",
      cell: (ndr) => (ndr.order_amount ? formatMoney(ndr.order_amount) : "—"),
    },
    {
      id: "payment_type",
      header: "Payment",
      cell: (ndr) => ndr.payment_type?.toUpperCase() ?? "—",
    },
    {
      id: "reason",
      header: "Reason",
      cell: (ndr) => ndr.reason ?? ndr.external_reason ?? "—",
    },
    { id: "attempt", header: "Attempt", cell: (ndr) => ndr.attempt_number },
    { id: "status", header: "Status", cell: (ndr) => <NdrStatusCell ndr={ndr} /> },
    {
      id: "reattempt_date",
      header: "Reattempt date",
      cell: (ndr) => (ndr.reattempt_date ? formatDate(ndr.reattempt_date) : "—"),
    },
    {
      id: "shipment_status",
      header: "Shipment status",
      cell: (ndr) =>
        ndr.shipment_status ? (
          <StatusBadge domain="shipment" status={ndr.shipment_status} />
        ) : (
          "—"
        ),
    },
    { id: "created", header: "Created", cell: (ndr) => formatDate(ndr.created_at) },
    { id: "action", header: "", cell: (ndr) => <NdrReattemptAction ndr={ndr} /> },
  ]

  return (
    <>
      <PageHeader title="NDR" description="Non-delivery reports awaiting resolution." />
      <div className="flex flex-col gap-4">
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
          <StatTile
            label="Total NDR"
            value={total ?? "—"}
            icon={ListChecks}
            accent="slate"
          />
          <StatTile
            label="Open / Pending"
            value={open ?? "—"}
            icon={AlertTriangle}
            accent="amber"
          />
          <StatTile
            label="Resolved"
            value={resolved ?? "—"}
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
          statusOptions={NDR_STATUS_OPTIONS}
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
          emptyTitle="No NDR records"
          emptyDescription="NDR records arrive here once a Shiprocket NDR sync runs, or via Shiprocket webhooks in a future phase."
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
                rowKey={(ndr) => ndr.id}
                onRowClick={(ndr) => router.push(`/orders/${ndr.order_id}`)}
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

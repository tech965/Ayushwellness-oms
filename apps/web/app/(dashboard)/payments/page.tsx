"use client"

import * as React from "react"
import { useRouter } from "next/navigation"
import { useQueryClient } from "@tanstack/react-query"
import { Columns3, Download } from "lucide-react"
import { toast } from "sonner"

import { CashfreeStatusCard } from "@/components/payments/cashfree-status-card"
import { PaymentMethodBreakdown } from "@/components/payments/payment-method-breakdown"
import { PaymentOverviewCards } from "@/components/payments/payment-overview-cards"
import { PaymentTrendChart } from "@/components/payments/payment-trend-chart"
import { DataTable, type DataTableColumn } from "@/components/shared/data-table"
import { DateRangePicker, type DateRangeValue } from "@/components/shared/date-range-picker"
import { FilterBar } from "@/components/shared/filter-bar"
import { PageHeader } from "@/components/shared/page-header"
import { PaginationBar } from "@/components/shared/pagination-bar"
import { QueryStates } from "@/components/shared/query-states"
import { StatusBadge } from "@/components/shared/status-badge"
import { Badge } from "@/components/ui/badge"
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Separator } from "@/components/ui/separator"
import { Skeleton } from "@/components/ui/skeleton"
import { getApiErrorMessage } from "@/lib/api-client"
import { useAuth } from "@/lib/auth-context"
import { formatDate, formatMoney } from "@/lib/format"
import { useLocalStorageState } from "@/lib/use-local-storage-state"
import { useUrlFilters } from "@/lib/use-url-filters"
import type { TimeseriesInterval } from "@/types/analytics"
import { useReconcileCashfreePayment } from "@/services/cashfree"
import {
  useExportPayments,
  usePaymentMethodBreakdown,
  usePaymentOverview,
  usePaymentTrend,
  usePayments,
} from "@/services/payments"
import {
  PAYMENT_PROVIDER_OPTIONS,
  PAYMENT_STATUS_OPTIONS,
  type Payment,
  type PaymentStatus,
} from "@/types/payment"

const FILTER_DEFAULTS = {
  q: "",
  provider: "",
  status: "",
  payment_method: "",
  date_from: "",
  date_to: "",
  sort_by: "",
  sort_order: "",
  page: 1,
  page_size: 20,
}

// A Cashfree payment sitting at PENDING this long may have had its
// webhook missed/delayed — surfaced as a subtle indicator + the existing
// per-order `POST /payments/cashfree/orders/{order_id}/reconcile` action,
// never auto-run (spec: no new reconciliation engine, no background
// auto-reconcile).
const NEEDS_RECONCILIATION_MS = 30 * 60 * 1000

function needsReconciliation(payment: Payment): boolean {
  if (payment.provider !== "cashfree" || payment.status !== "pending") return false
  return Date.now() - new Date(payment.created_at).getTime() > NEEDS_RECONCILIATION_MS
}

/** Own component (not an inline closure) so `useReconcileCashfreePayment`
 * -- a real hook -- is called once per row's own component instance,
 * never inside a plain callback (Rules of Hooks). Reuses the exact same
 * per-order reconcile endpoint/hook the order-detail Cashfree card uses;
 * only additionally invalidates the payments list/detail queries so this
 * table reflects the result without a manual refresh.
 */
function PaymentRowActions({ payment }: { payment: Payment }) {
  const { hasPermission } = useAuth()
  const queryClient = useQueryClient()
  const reconcile = useReconcileCashfreePayment(payment.order_id)

  if (payment.provider !== "cashfree" || payment.status !== "pending") {
    return null
  }
  if (!hasPermission("payments.create")) return null

  return (
    <Button
      variant="outline"
      size="sm"
      disabled={reconcile.isPending}
      onClick={(event) => {
        event.stopPropagation()
        reconcile.mutate(undefined, {
          onSuccess: () => {
            toast.success("Reconciled against Cashfree.")
            void queryClient.invalidateQueries({ queryKey: ["payments"] })
          },
          onError: (error) => toast.error(getApiErrorMessage(error)),
        })
      }}
    >
      {reconcile.isPending ? "Reconciling..." : "Reconcile"}
    </Button>
  )
}

interface ColumnDef {
  id: string
  label: string
  defaultVisible: boolean
  column: DataTableColumn<Payment>
}

const ALL_COLUMNS: ColumnDef[] = [
  {
    id: "id",
    label: "Payment ID",
    defaultVisible: true,
    column: {
      id: "id",
      header: "Payment ID",
      cell: (p) => <span className="font-mono text-xs">{p.id.slice(0, 8)}</span>,
    },
  },
  {
    id: "order_number",
    label: "Order Number",
    defaultVisible: true,
    column: {
      id: "order_number",
      header: "Order Number",
      cell: (p) => <span className="font-medium">{p.order_number ?? "—"}</span>,
    },
  },
  {
    id: "customer",
    label: "Customer",
    defaultVisible: true,
    column: {
      id: "customer",
      header: "Customer",
      cell: (p) => p.customer_name ?? "—",
    },
  },
  {
    id: "amount",
    label: "Amount",
    defaultVisible: true,
    column: {
      id: "amount",
      header: "Amount",
      className: "text-right",
      sortKey: "amount",
      cell: (p) => formatMoney(p.amount, p.currency),
    },
  },
  {
    id: "payment_method",
    label: "Payment Method",
    defaultVisible: true,
    column: {
      id: "payment_method",
      header: "Payment Method",
      cell: (p) =>
        p.payment_method ? (
          <StatusBadge domain="payment_method" status={p.payment_method} />
        ) : (
          "—"
        ),
    },
  },
  {
    id: "provider",
    label: "Provider",
    defaultVisible: true,
    column: {
      id: "provider",
      header: "Provider",
      cell: (p) => (p.provider ? p.provider.toUpperCase() : "—"),
    },
  },
  {
    id: "status",
    label: "Status",
    defaultVisible: true,
    column: {
      id: "status",
      header: "Status",
      cell: (p) => (
        <div className="flex flex-wrap items-center gap-1.5">
          <StatusBadge domain="payment" status={p.status} />
          {needsReconciliation(p) && (
            <Badge
              variant="outline"
              className="border-transparent bg-amber-50 text-[0.625rem] text-amber-700 dark:bg-amber-500/15 dark:text-amber-400"
            >
              Needs Reconciliation
            </Badge>
          )}
        </div>
      ),
    },
  },
  {
    id: "created_at",
    label: "Created At",
    defaultVisible: true,
    column: {
      id: "created_at",
      header: "Created At",
      sortKey: "created_at",
      cell: (p) => formatDate(p.created_at),
    },
  },
  {
    id: "paid_at",
    label: "Paid At",
    defaultVisible: false,
    column: {
      id: "paid_at",
      header: "Paid At",
      cell: (p) => (p.paid_at ? formatDate(p.paid_at) : "—"),
    },
  },
  {
    id: "cashfree_order_id",
    label: "Cashfree Order ID",
    defaultVisible: false,
    column: {
      id: "cashfree_order_id",
      header: "Cashfree Order ID",
      cell: (p) => <span className="font-mono text-xs">{p.external_id ?? "—"}</span>,
    },
  },
  {
    id: "actions",
    label: "Actions",
    defaultVisible: true,
    column: {
      id: "actions",
      header: "Actions",
      cell: (p) => <PaymentRowActions payment={p} />,
    },
  },
]

const COLUMN_STORAGE_KEY = "oms_payments_visible_columns"
const DEFAULT_VISIBLE_COLUMN_IDS = ALL_COLUMNS.filter((c) => c.defaultVisible).map(
  (c) => c.id
)

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

function useDebouncedValue<T>(value: T, delayMs: number): T {
  const [debounced, setDebounced] = React.useState(value)
  React.useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delayMs)
    return () => clearTimeout(timer)
  }, [value, delayMs])
  return debounced
}

function PaymentsSkeleton() {
  return (
    <>
      <PageHeader title="Payments" description="Cashfree and other payment activity across the OMS." />
      <div className="flex flex-col gap-4">
        <Skeleton className="h-24 w-full" />
        <div className="grid grid-cols-2 gap-4 lg:grid-cols-5">
          {Array.from({ length: 5 }).map((_, i) => (
            <Skeleton key={i} className="h-24 w-full" />
          ))}
        </div>
        {Array.from({ length: 6 }).map((_, i) => (
          <Skeleton key={i} className="h-10 w-full" />
        ))}
      </div>
    </>
  )
}

export default function PaymentsPage() {
  return (
    <React.Suspense fallback={<PaymentsSkeleton />}>
      <PaymentsPageContent />
    </React.Suspense>
  )
}

function PaymentsPageContent() {
  const router = useRouter()
  const { filters, setFilters, clearFilters } = useUrlFilters(FILTER_DEFAULTS)
  const [visibleColumns, toggleColumn] = useVisibleColumns()
  const exportMutation = useExportPayments()
  const [interval, setInterval] = React.useState<TimeseriesInterval>("day")

  const [searchInput, setSearchInput] = React.useState(filters.q)
  const [paymentMethodInput, setPaymentMethodInput] = React.useState(filters.payment_method)
  const debouncedSearch = useDebouncedValue(searchInput, 400)
  const debouncedPaymentMethod = useDebouncedValue(paymentMethodInput, 400)

  React.useEffect(() => {
    setFilters({ q: debouncedSearch })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debouncedSearch])
  React.useEffect(() => {
    setFilters({ payment_method: debouncedPaymentMethod })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debouncedPaymentMethod])

  const dateRange: DateRangeValue = {
    from: filters.date_from ? new Date(filters.date_from) : undefined,
    to: filters.date_to ? new Date(filters.date_to) : undefined,
  }

  const activeFilters = {
    q: filters.q || undefined,
    provider: filters.provider || undefined,
    status: (filters.status || undefined) as PaymentStatus | undefined,
    payment_method: filters.payment_method || undefined,
    date_from: filters.date_from || undefined,
    date_to: filters.date_to || undefined,
  }

  const query = usePayments({
    page: filters.page,
    pageSize: filters.page_size,
    sortBy: filters.sort_by || undefined,
    sortOrder: (filters.sort_order || undefined) as "asc" | "desc" | undefined,
    ...activeFilters,
  })

  // The KPI cards/trend/method-breakdown share the table's own date
  // range AND provider filter, so they always describe exactly the same
  // slice of data the rows below them show -- same contract the main
  // Dashboard's KPIs/drill-down links already use. Provider-agnostic by
  // default ("All providers" -> every payment, Shopify included); the
  // dedicated Cashfree-only analytics endpoints this page used to call
  // are untouched and still power nothing here now, on purpose.
  const analyticsParams = {
    provider: filters.provider || undefined,
    date_from: filters.date_from || undefined,
    date_to: filters.date_to || undefined,
  }
  const overviewQuery = usePaymentOverview(analyticsParams)
  const trendQuery = usePaymentTrend({ ...analyticsParams, interval })
  const methodBreakdownQuery = usePaymentMethodBreakdown(analyticsParams)

  function hrefFor(extra: Record<string, string>): string {
    const params = new URLSearchParams()
    // Mirrors whatever provider the KPI cards themselves are currently
    // aggregating over (`analyticsParams.provider` above) -- "All
    // providers" (no filter selected) must drill down to no `provider`
    // param at all, not a stale hardcoded "cashfree" from when these
    // cards were Cashfree-only. Never widens/narrows the provider beyond
    // whatever context the cards are already showing.
    if (filters.provider) params.set("provider", filters.provider)
    if (filters.date_from) params.set("date_from", filters.date_from)
    if (filters.date_to) params.set("date_to", filters.date_to)
    for (const [key, value] of Object.entries(extra)) params.set(key, value)
    return `/payments?${params.toString()}`
  }

  function handleSortChange(sortKey: string) {
    if (filters.sort_by !== sortKey) {
      setFilters({ sort_by: sortKey, sort_order: "desc" })
    } else {
      setFilters({ sort_order: filters.sort_order === "asc" ? "desc" : "asc" })
    }
  }

  function handleClear() {
    setSearchInput("")
    setPaymentMethodInput("")
    clearFilters()
  }

  function handleExport() {
    exportMutation.mutate(activeFilters, {
      onError: (error) => toast.error(getApiErrorMessage(error)),
    })
  }

  const columns = ALL_COLUMNS.filter((c) => visibleColumns.has(c.id)).map((c) => c.column)

  return (
    <>
      <PageHeader
        title="Payments"
        description={
          query.data
            ? `${query.data.meta.total_items} payments match the current filters.`
            : "Cashfree and other payment activity across the OMS."
        }
        backHref="/dashboard"
        backLabel="Back to Dashboard"
        actions={
          <>
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
          </>
        }
      />

      <div className="flex flex-col gap-6">
        <CashfreeStatusCard />

        <PaymentOverviewCards data={overviewQuery.data} hrefFor={hrefFor} />

        <div className="grid gap-4 lg:grid-cols-2">
          <PaymentTrendChart
            data={trendQuery.data}
            interval={interval}
            onIntervalChange={setInterval}
            isLoading={trendQuery.isLoading}
          />
          <PaymentMethodBreakdown
            data={methodBreakdownQuery.data}
            isLoading={methodBreakdownQuery.isLoading}
            hrefFor={(method) => hrefFor({ payment_method: method })}
          />
        </div>

        <div className="bg-card border-border flex flex-col gap-3 rounded-lg border p-3">
          <FilterBar
            className="border-none p-0"
            searchValue={searchInput}
            onSearchChange={setSearchInput}
            searchPlaceholder="Search by order #, customer, payment ID..."
            statusValue={filters.status || undefined}
            onStatusChange={(value) => setFilters({ status: value ?? "" })}
            statusOptions={PAYMENT_STATUS_OPTIONS}
            statusLabel="Payment Status"
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
            <Select
              value={filters.provider || "__all__"}
              onValueChange={(v) => setFilters({ provider: v === "__all__" ? "" : v })}
            >
              <SelectTrigger className="w-[150px]">
                <SelectValue placeholder="Provider" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="__all__">All providers</SelectItem>
                {PAYMENT_PROVIDER_OPTIONS.map((o) => (
                  <SelectItem key={o.value} value={o.value}>
                    {o.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>

            <Input
              value={paymentMethodInput}
              onChange={(e) => setPaymentMethodInput(e.target.value)}
              placeholder="Payment method (upi, card...)"
              className="w-[200px]"
            />

            <Button variant="ghost" size="sm" onClick={handleClear}>
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
          emptyTitle="No payments found"
          emptyDescription="Try adjusting your search or filters."
        >
          {(data) => (
            <>
              <DataTable
                columns={columns}
                data={data.data}
                rowKey={(p) => p.id}
                onRowClick={(p) => router.push(`/payments/${p.id}`)}
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

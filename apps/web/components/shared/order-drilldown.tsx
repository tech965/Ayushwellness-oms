"use client"

import * as React from "react"
import { useRouter } from "next/navigation"
import { ArrowLeft, Search } from "lucide-react"

import { DataTable, type DataTableColumn } from "@/components/shared/data-table"
import { PaginationBar } from "@/components/shared/pagination-bar"
import { QueryStates } from "@/components/shared/query-states"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import type { PaginatedResponse } from "@/types/api"

interface DrilldownQuery<TRow> {
  data: PaginatedResponse<TRow> | undefined
  isLoading: boolean
  isError: boolean
  error?: unknown
  refetch: () => unknown
}

interface OrderDrilldownProps<TRow extends { order_id: string }> {
  title: string
  description?: string
  query: DrilldownQuery<TRow>
  columns: DataTableColumn<TRow>[]
  /** Raw search text; the parent owns it so it can reset on selection change. */
  search: string
  onSearchChange: (value: string) => void
  onPageChange: (page: number) => void
  onClear: () => void
  emptyTitle?: string
  /** Extra filter controls (e.g. a status select), rendered beside search. */
  filters?: React.ReactNode
}

/** The order list that opens under a clicked KPI card, chart bar or table
 * row: title, Back/Clear, search, server-paginated table with loading /
 * empty / error states. Clicking a row opens that order's own detail page.
 */
export function OrderDrilldown<TRow extends { order_id: string }>({
  title,
  description,
  query,
  columns,
  search,
  onSearchChange,
  onPageChange,
  onClear,
  emptyTitle = "No orders match this selection",
  filters,
}: OrderDrilldownProps<TRow>) {
  const router = useRouter()
  const total = query.data?.meta.total_items

  return (
    <section
      className="bg-card space-y-3 rounded-xl border p-4"
      aria-label={title}
      data-testid="order-drilldown"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="text-base font-semibold">
            {title}
            {total !== undefined && (
              <span className="text-muted-foreground ml-2 text-sm font-normal tabular-nums">
                {total} {total === 1 ? "order" : "orders"}
              </span>
            )}
          </h3>
          {description && <p className="text-muted-foreground text-xs">{description}</p>}
        </div>
        <Button variant="outline" size="sm" onClick={onClear}>
          <ArrowLeft className="size-4" />
          Clear selection
        </Button>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <div className="relative w-full max-w-xs">
          <Search className="text-muted-foreground absolute top-2.5 left-2.5 size-4" />
          <Input
            value={search}
            onChange={(event) => onSearchChange(event.target.value)}
            placeholder="Search order or tracking number"
            aria-label="Search orders"
            className="pl-8"
          />
        </div>
        {filters}
      </div>

      <QueryStates
        isLoading={query.isLoading}
        isError={query.isError}
        error={query.error}
        data={query.data}
        onRetry={() => void query.refetch()}
        isEmpty={(result) => result.data.length === 0}
        emptyTitle={emptyTitle}
      >
        {(result) => (
          <>
            <DataTable
              columns={columns}
              data={result.data}
              rowKey={(row) => row.order_id}
              onRowClick={(row) => router.push(`/orders/${row.order_id}`)}
            />
            <PaginationBar meta={result.meta} onPageChange={onPageChange} />
          </>
        )}
      </QueryStates>
    </section>
  )
}

/** Debounces a fast-changing value (search text) so each keystroke does not
 * hit the API.
 */
export function useDebouncedValue<T>(value: T, delayMs = 300): T {
  const [debounced, setDebounced] = React.useState(value)
  React.useEffect(() => {
    const id = window.setTimeout(() => setDebounced(value), delayMs)
    return () => window.clearTimeout(id)
  }, [value, delayMs])
  return debounced
}

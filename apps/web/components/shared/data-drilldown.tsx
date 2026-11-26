"use client"

import * as React from "react"
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

interface DataDrilldownProps<TRow> {
  title: string
  description?: string
  query: DrilldownQuery<TRow>
  columns: DataTableColumn<TRow>[]
  rowKey: (row: TRow) => string
  onRowClick?: (row: TRow) => void
  search: string
  onSearchChange: (value: string) => void
  onPageChange: (page: number) => void
  onClear: () => void
  searchPlaceholder?: string
  emptyTitle?: string
  /** Filters / totals shown above the search box. */
  header?: React.ReactNode
}

/** The record list that opens under a clicked KPI, percentage, chart element
 * or table row: title with the exact count, search, Back/Clear, server
 * pagination and loading / empty / error states. Generic over the row type
 * (orders, remittance lines, bank credits, ...).
 */
export function DataDrilldown<TRow>({
  title,
  description,
  query,
  columns,
  rowKey,
  onRowClick,
  search,
  onSearchChange,
  onPageChange,
  onClear,
  searchPlaceholder = "Search",
  emptyTitle = "No records match this selection",
  header,
}: DataDrilldownProps<TRow>) {
  const total = query.data?.meta.total_items
  return (
    <section
      className="bg-card min-w-0 space-y-3 rounded-xl border p-4"
      aria-label={title}
      data-testid="record-drilldown"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="text-base font-semibold">
            {title}
            {total !== undefined && (
              <span className="text-muted-foreground ml-2 text-sm font-normal tabular-nums">
                {total} {total === 1 ? "record" : "records"}
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
      {header}
      <div className="relative w-full max-w-xs">
        <Search className="text-muted-foreground absolute top-2.5 left-2.5 size-4" />
        <Input
          value={search}
          onChange={(event) => onSearchChange(event.target.value)}
          placeholder={searchPlaceholder}
          aria-label="Search records"
          className="pl-8"
        />
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
              rowKey={rowKey}
              onRowClick={onRowClick}
            />
            <PaginationBar meta={result.meta} onPageChange={onPageChange} />
          </>
        )}
      </QueryStates>
    </section>
  )
}

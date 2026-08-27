import type { ReactNode } from "react"
import { ArrowDown, ArrowUp, ArrowUpDown } from "lucide-react"

import { Checkbox } from "@/components/ui/checkbox"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { cn } from "@/lib/utils"

export interface DataTableColumn<TRow> {
  id: string
  header: ReactNode
  cell: (row: TRow) => ReactNode
  className?: string
  /** Backend column name this header sorts by (e.g. `"order_datetime"`).
   * Omit for columns that aren't sortable (e.g. a computed summary column).
   */
  sortKey?: string
}

export interface DataTableSelection {
  selectedIds: Set<string>
  onToggle: (id: string) => void
  /** Called with every currently-rendered row's id — toggles select-all
   * for the *current page* only (bulk-selecting beyond one page is an
   * explicit "Select all N" affordance the caller can add separately).
   */
  onToggleAll: (ids: string[]) => void
}

interface DataTableProps<TRow> {
  columns: DataTableColumn<TRow>[]
  data: TRow[]
  rowKey: (row: TRow) => string
  onRowClick?: (row: TRow) => void
  sortBy?: string
  sortOrder?: "asc" | "desc"
  onSortChange?: (sortKey: string) => void
  /** Renders a leading checkbox column when provided — every existing
   * caller omits this and is unaffected (bulk-assignment UI is the only
   * consumer today).
   */
  selection?: DataTableSelection
}

/**
 * Plain column-definition table shared by every list page. Sorting and
 * pagination are server-driven (query params against the backend, see
 * PaginationBar/FilterBar) rather than client-side, so this deliberately
 * doesn't pull in a headless table library — it only needs to render rows
 * and, for columns with a `sortKey`, a clickable header that reports the
 * next sort state back to the caller.
 */
export function DataTable<TRow>({
  columns,
  data,
  rowKey,
  onRowClick,
  sortBy,
  sortOrder,
  onSortChange,
  selection,
}: DataTableProps<TRow>) {
  const rowIds = data.map(rowKey)
  const allSelected =
    rowIds.length > 0 && rowIds.every((id) => selection?.selectedIds.has(id))
  const someSelected = !allSelected && rowIds.some((id) => selection?.selectedIds.has(id))

  return (
    <div className="overflow-x-auto rounded-lg border">
      <Table>
        <TableHeader className="bg-muted/40">
          <TableRow className="hover:bg-transparent">
            {selection && (
              <TableHead className="w-10">
                <Checkbox
                  checked={allSelected ? true : someSelected ? "indeterminate" : false}
                  onCheckedChange={() => selection.onToggleAll(rowIds)}
                  aria-label="Select all rows on this page"
                />
              </TableHead>
            )}
            {columns.map((column) => {
              const isSortable = Boolean(column.sortKey && onSortChange)
              const isActive = isSortable && column.sortKey === sortBy
              const Icon = isActive
                ? sortOrder === "asc"
                  ? ArrowUp
                  : ArrowDown
                : ArrowUpDown

              return (
                <TableHead
                  key={column.id}
                  className={cn(
                    "text-muted-foreground text-xs font-semibold tracking-wide uppercase",
                    column.className
                  )}
                >
                  {isSortable ? (
                    <button
                      type="button"
                      onClick={() => onSortChange?.(column.sortKey!)}
                      className={cn(
                        "hover:text-primary -mx-1 flex items-center gap-1 rounded px-1 py-0.5 transition-colors",
                        column.className?.includes("text-right") &&
                          "ml-auto flex-row-reverse"
                      )}
                    >
                      {column.header}
                      <Icon
                        className={cn(
                          "size-3.5",
                          isActive ? "text-primary" : "opacity-40"
                        )}
                      />
                    </button>
                  ) : (
                    column.header
                  )}
                </TableHead>
              )
            })}
          </TableRow>
        </TableHeader>
        <TableBody>
          {data.map((row) => (
            <TableRow
              key={rowKey(row)}
              onClick={() => onRowClick?.(row)}
              className={cn(onRowClick && "hover:bg-accent/60 cursor-pointer")}
            >
              {selection && (
                <TableCell onClick={(event) => event.stopPropagation()}>
                  <Checkbox
                    checked={selection.selectedIds.has(rowKey(row))}
                    onCheckedChange={() => selection.onToggle(rowKey(row))}
                    aria-label="Select row"
                  />
                </TableCell>
              )}
              {columns.map((column) => (
                <TableCell key={column.id} className={column.className}>
                  {column.cell(row)}
                </TableCell>
              ))}
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  )
}

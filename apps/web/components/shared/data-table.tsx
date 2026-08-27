import type { ReactNode } from "react"
import { ArrowDown, ArrowUp, ArrowUpDown } from "lucide-react"

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

interface DataTableProps<TRow> {
  columns: DataTableColumn<TRow>[]
  data: TRow[]
  rowKey: (row: TRow) => string
  onRowClick?: (row: TRow) => void
  sortBy?: string
  sortOrder?: "asc" | "desc"
  onSortChange?: (sortKey: string) => void
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
}: DataTableProps<TRow>) {
  return (
    <div className="overflow-x-auto rounded-lg border">
      <Table>
        <TableHeader className="bg-muted/40">
          <TableRow className="hover:bg-transparent">
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

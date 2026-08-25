import type { ReactNode } from "react"

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
}

interface DataTableProps<TRow> {
  columns: DataTableColumn<TRow>[]
  data: TRow[]
  rowKey: (row: TRow) => string
  onRowClick?: (row: TRow) => void
}

/**
 * Plain column-definition table shared by every list page. Sorting and
 * pagination are server-driven (query params against the backend, see
 * PaginationBar/FilterBar) rather than client-side, so this deliberately
 * doesn't pull in a headless table library — it only needs to render rows.
 */
export function DataTable<TRow>({
  columns,
  data,
  rowKey,
  onRowClick,
}: DataTableProps<TRow>) {
  return (
    <div className="overflow-x-auto rounded-md border">
      <Table>
        <TableHeader>
          <TableRow>
            {columns.map((column) => (
              <TableHead key={column.id} className={column.className}>
                {column.header}
              </TableHead>
            ))}
          </TableRow>
        </TableHeader>
        <TableBody>
          {data.map((row) => (
            <TableRow
              key={rowKey(row)}
              onClick={() => onRowClick?.(row)}
              className={cn(onRowClick && "cursor-pointer")}
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

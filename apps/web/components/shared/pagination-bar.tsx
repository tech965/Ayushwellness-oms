import { ChevronLeft, ChevronRight } from "lucide-react"

import { Button } from "@/components/ui/button"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import type { PaginationMeta } from "@/types/api"

interface PaginationBarProps {
  meta: PaginationMeta
  onPageChange: (page: number) => void
  pageSizeOptions?: number[]
  onPageSizeChange?: (pageSize: number) => void
}

/** Shared page/page_size control against `PaginationMeta` — every list
 * page renders this instead of reimplementing pagination (spec §37).
 * `onPageSizeChange` is optional so existing callers keep working
 * unchanged; pass it (with `pageSizeOptions`) to also show a rows-per-page
 * selector.
 */
export function PaginationBar({
  meta,
  onPageChange,
  pageSizeOptions,
  onPageSizeChange,
}: PaginationBarProps) {
  const showPageSize = Boolean(pageSizeOptions?.length && onPageSizeChange)
  if (meta.total_pages <= 1 && !showPageSize) return null

  const canPrev = meta.page > 1
  const canNext = meta.page < meta.total_pages
  const rangeStart = meta.total_items === 0 ? 0 : (meta.page - 1) * meta.page_size + 1
  const rangeEnd = Math.min(meta.page * meta.page_size, meta.total_items)

  return (
    <div className="flex flex-wrap items-center justify-between gap-3 border-t pt-3">
      <div className="flex items-center gap-3">
        <p className="text-muted-foreground text-sm">
          {meta.total_items === 0 ? (
            "No results"
          ) : (
            <>
              Showing{" "}
              <span className="text-foreground font-medium tabular-nums">
                {rangeStart}–{rangeEnd}
              </span>{" "}
              of{" "}
              <span className="text-foreground font-medium tabular-nums">
                {meta.total_items}
              </span>
            </>
          )}
        </p>
        {showPageSize && (
          <Select
            value={String(meta.page_size)}
            onValueChange={(value) => onPageSizeChange?.(Number(value))}
          >
            <SelectTrigger className="w-[110px]" size="sm">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {pageSizeOptions?.map((size) => (
                <SelectItem key={size} value={String(size)}>
                  {size} / page
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}
      </div>
      {meta.total_pages > 1 && (
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            disabled={!canPrev}
            onClick={() => onPageChange(meta.page - 1)}
          >
            <ChevronLeft className="size-4" />
            Previous
          </Button>
          <Button
            variant="outline"
            size="sm"
            disabled={!canNext}
            onClick={() => onPageChange(meta.page + 1)}
          >
            Next
            <ChevronRight className="size-4" />
          </Button>
        </div>
      )}
    </div>
  )
}

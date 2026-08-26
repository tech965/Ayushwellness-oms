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

  return (
    <div className="flex flex-wrap items-center justify-between gap-3 border-t pt-3">
      <div className="flex items-center gap-3">
        <p className="text-muted-foreground text-sm">
          Page {meta.page} of {Math.max(meta.total_pages, 1)} &middot; {meta.total_items} total
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

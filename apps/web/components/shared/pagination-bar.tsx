import { ChevronLeft, ChevronRight } from "lucide-react"

import { Button } from "@/components/ui/button"
import type { PaginationMeta } from "@/types/api"

interface PaginationBarProps {
  meta: PaginationMeta
  onPageChange: (page: number) => void
}

/** Shared page/page_size control against `PaginationMeta` — every list
 * page renders this instead of reimplementing pagination (spec §37).
 */
export function PaginationBar({ meta, onPageChange }: PaginationBarProps) {
  if (meta.total_pages <= 1) return null

  const canPrev = meta.page > 1
  const canNext = meta.page < meta.total_pages

  return (
    <div className="flex items-center justify-between border-t pt-3">
      <p className="text-muted-foreground text-sm">
        Page {meta.page} of {meta.total_pages} &middot; {meta.total_items} total
      </p>
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
    </div>
  )
}

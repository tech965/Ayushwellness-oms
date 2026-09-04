"use client"

import type { ReactNode } from "react"
import { AlertCircle } from "lucide-react"

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { getApiErrorMessage } from "@/lib/api-client"

interface QueryStatesProps<T> {
  isLoading: boolean
  isError: boolean
  error?: unknown
  data: T | undefined
  onRetry?: () => void
  isEmpty?: (data: T) => boolean
  emptyTitle?: string
  emptyDescription?: string
  skeletonRows?: number
  children: (data: T) => ReactNode
}

/**
 * Consistent loading/error/empty handling for any TanStack Query result —
 * every list/detail page renders this instead of reimplementing the same
 * three states (spec §48: no page may show a blank screen).
 *
 * Pre-demo fix: already-loaded `data` is now checked BEFORE `isError` --
 * previously, a background refetch that failed on an already-populated
 * query (e.g. navigating back to a page whose cached data is a few
 * seconds stale, TanStack Query revalidates it in the background, and
 * that one revalidation happens to fail) replaced the real, still-valid
 * content with the full error Alert, which read as the whole page
 * "blinking" back to a blank/error state during ordinary navigation.
 * `data` that's already on screen must never be yanked away by a
 * transient refresh error -- only a query that has NEVER successfully
 * loaded anything shows the blocking error state; once real data exists,
 * a failed background refresh is surfaced as a small, non-blocking
 * notice above the unchanged content instead.
 */
export function QueryStates<T>({
  isLoading,
  isError,
  error,
  data,
  onRetry,
  isEmpty,
  emptyTitle = "Nothing here yet",
  emptyDescription,
  skeletonRows = 5,
  children,
}: QueryStatesProps<T>) {
  if (isLoading) {
    return (
      <div className="flex flex-col gap-2">
        {Array.from({ length: skeletonRows }).map((_, index) => (
          <Skeleton key={index} className="h-10 w-full" />
        ))}
      </div>
    )
  }

  const hasData = data !== undefined && !(isEmpty ? isEmpty(data) : false)

  if (hasData) {
    return (
      <>
        {isError && (
          <div className="border-border bg-muted/50 text-muted-foreground mb-3 flex items-center justify-between gap-3 rounded-md border px-3 py-2 text-xs">
            <span>Could not refresh — showing the last loaded data.</span>
            {onRetry && (
              <Button variant="ghost" size="sm" onClick={onRetry} className="h-6 px-2 text-xs">
                Retry
              </Button>
            )}
          </div>
        )}
        {children(data as T)}
      </>
    )
  }

  if (isError) {
    return (
      <Alert variant="destructive">
        <AlertCircle className="size-4" />
        <AlertTitle>Something went wrong</AlertTitle>
        <AlertDescription className="flex flex-col gap-3">
          <span>{getApiErrorMessage(error)}</span>
          {onRetry && (
            <Button variant="outline" size="sm" onClick={onRetry} className="w-fit">
              Retry
            </Button>
          )}
        </AlertDescription>
      </Alert>
    )
  }

  return (
    <div className="border-border flex flex-col items-center gap-1 rounded-md border border-dashed py-16 text-center">
      <p className="text-foreground text-sm font-medium">{emptyTitle}</p>
      {emptyDescription && (
        <p className="text-muted-foreground max-w-sm text-sm">{emptyDescription}</p>
      )}
    </div>
  )
}

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

  if (data === undefined || (isEmpty ? isEmpty(data) : false)) {
    return (
      <div className="border-border flex flex-col items-center gap-1 rounded-md border border-dashed py-16 text-center">
        <p className="text-foreground text-sm font-medium">{emptyTitle}</p>
        {emptyDescription && (
          <p className="text-muted-foreground max-w-sm text-sm">{emptyDescription}</p>
        )}
      </div>
    )
  }

  return <>{children(data)}</>
}

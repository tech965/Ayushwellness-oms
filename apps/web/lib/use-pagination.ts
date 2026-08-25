import * as React from "react"

/** Local page-number state shared by every list page — resetPage() is
 * called whenever a filter changes so a stale page 4 doesn't silently
 * return an empty result set after narrowing the filters.
 */
export function usePaginationState(pageSize = 20) {
  const [page, setPage] = React.useState(1)
  const resetPage = React.useCallback(() => setPage(1), [])
  return { page, pageSize, setPage, resetPage }
}

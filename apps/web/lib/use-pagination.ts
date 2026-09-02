import * as React from "react"

import { useAppSettings } from "@/lib/settings-context"

/** Local page-number state shared by every list page — resetPage() is
 * called whenever a filter changes so a stale page 4 doesn't silently
 * return an empty result set after narrowing the filters.
 *
 * `pageSize` is optional: when the caller doesn't pin one, it falls back
 * to the OMS-wide "Default pagination size" setting (Administration ->
 * Settings -> General), so changing that setting actually changes every
 * page that doesn't have its own explicit page-size control. Falls back
 * further to 20 whenever settings haven't loaded yet (or there's no
 * `SettingsProvider` in the tree at all, e.g. a component rendered in
 * isolation in a test) -- never blocks on or triggers its own fetch.
 */
export function usePaginationState(pageSize?: number) {
  const settings = useAppSettings()
  const effectivePageSize = pageSize ?? settings?.general.default_page_size ?? 20
  const [page, setPage] = React.useState(1)
  const resetPage = React.useCallback(() => setPage(1), [])
  return { page, pageSize: effectivePageSize, setPage, resetPage }
}

"use client"

import * as React from "react"
import { usePathname, useRouter, useSearchParams } from "next/navigation"

type FilterRecord = Record<string, string | number | undefined>

/**
 * Syncs a flat set of filter/pagination values to the URL query string
 * (`router.replace`, no scroll/history-entry spam) so list pages survive a
 * refresh, are deep-linkable from the Dashboard's drill-down links, and
 * restore correctly on browser back/forward — none of which plain
 * `useState` filters can do (see Orders page redesign).
 *
 * `defaults` must be a stable reference (module-level constant or
 * `useMemo`) — it's a dependency of the read/write callbacks below.
 */
export function useUrlFilters<T extends FilterRecord>(defaults: T) {
  const router = useRouter()
  const pathname = usePathname()
  const searchParams = useSearchParams()

  const filters = React.useMemo(() => {
    const result = { ...defaults }
    for (const key of Object.keys(defaults) as (keyof T)[]) {
      const raw = searchParams.get(String(key))
      if (raw === null || raw === "") continue
      const defaultValue = defaults[key]
      result[key] =
        typeof defaultValue === "number"
          ? (Number(raw) as T[keyof T])
          : (raw as T[keyof T])
    }
    return result
  }, [searchParams, defaults])

  const setFilters = React.useCallback(
    (patch: Partial<T>) => {
      const params = new URLSearchParams(searchParams.toString())
      for (const key of Object.keys(patch) as (keyof T)[]) {
        const value = patch[key]
        if (value === undefined || value === "" || value === defaults[key]) {
          params.delete(String(key))
        } else {
          params.set(String(key), String(value))
        }
      }
      // Any filter change other than an explicit page change invalidates
      // whatever page the user was on — matches `usePaginationState`'s
      // `resetPage()` convention this hook replaces.
      if (!("page" in patch)) params.delete("page")
      const query = params.toString()
      // Pre-demo fix: several pages (Orders, Payments) sync a debounced
      // text input back to the URL via `useEffect(() => setFilters({q:
      // debounced}), [debounced])` -- that effect always fires once on
      // mount, with the value the URL already has, since a dependency
      // "changing" from its initial undefined state still counts as a
      // change to React. Without this check, every single mount of those
      // pages fired a real `router.replace` (a same-document History API
      // navigation) that changed nothing. Across a demo session of
      // repeated back-and-forth navigation between pages, these
      // genuinely-no-op navigations added up and were the concrete,
      // reproducible source of enough same-document navigations to trip
      // Chrome's own "Throttling navigation" rate limiter. Skipping a
      // call that would produce the exact same URL the address bar
      // already shows changes no observable behavior for a real filter
      // change (query only equals the current string when nothing this
      // patch does would alter it).
      if (query === searchParams.toString()) return
      router.replace(query ? `${pathname}?${query}` : pathname, { scroll: false })
    },
    [defaults, pathname, router, searchParams]
  )

  const clearFilters = React.useCallback(() => {
    router.replace(pathname, { scroll: false })
  }, [pathname, router])

  return { filters, setFilters, clearFilters, queryString: searchParams.toString() }
}

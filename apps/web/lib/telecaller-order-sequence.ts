/** Requirement 2 (review meeting): J/K next/previous-order navigation on
 * the Telecaller order-detail page. The detail page (`/telecaller/orders/
 * [id]`) has no independent knowledge of "what list/sort/filter the
 * telecaller was looking at" — it's reached by a bare order id in the
 * URL. The list page (`/telecaller/orders`) is the only place that knows
 * the actual current sequence (whatever `GET /telecaller/orders` just
 * returned, in the order it returned it, honoring the telecaller's
 * active filters/search), so it records that sequence here every time it
 * loads a page of results; the detail page reads it back to compute
 * next/previous without re-deriving or guessing the ordering itself.
 *
 * `sessionStorage`, not `localStorage`: this is throwaway navigation
 * context for the current tab/session, not a durable preference — it
 * should not silently resurrect a stale filter/page across days or leak
 * between browser profiles.
 *
 * Best-effort only (wrapped in try/catch): unavailable storage (private
 * browsing, quota, disabled) degrades to "no stored sequence," which the
 * consumer already treats as a normal, harmless "nothing to navigate to
 * yet" state — never a crash.
 */

const STORAGE_KEY = "telecaller-order-sequence"

export interface OrderSequenceFilters {
  call_status?: string
  date_from?: string
  date_to?: string
  q?: string
  pageSize: number
}

export interface OrderSequenceState {
  ids: string[]
  page: number
  totalPages: number
  filters: OrderSequenceFilters
}

export function saveOrderSequence(state: OrderSequenceState): void {
  try {
    window.sessionStorage.setItem(STORAGE_KEY, JSON.stringify(state))
  } catch {
    // Best-effort only — see module docstring.
  }
}

export function readOrderSequence(): OrderSequenceState | null {
  try {
    const raw = window.sessionStorage.getItem(STORAGE_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw) as OrderSequenceState
    if (!Array.isArray(parsed.ids) || typeof parsed.page !== "number") return null
    return parsed
  } catch {
    return null
  }
}

/** Merges freshly-fetched page data into whatever sequence is already
 * stored, rather than blindly overwriting it — a J/K crossing into an
 * adjacent page appends/prepends that page's ids onto the existing
 * sequence (see `use-order-navigation.ts`) instead of collapsing back
 * down to a single page every time.
 */
export function mergeOrderSequencePage(
  existing: OrderSequenceState | null,
  page: { ids: string[]; page: number; totalPages: number; filters: OrderSequenceFilters }
): OrderSequenceState {
  const sameQuery =
    existing &&
    existing.filters.call_status === page.filters.call_status &&
    existing.filters.date_from === page.filters.date_from &&
    existing.filters.date_to === page.filters.date_to &&
    existing.filters.q === page.filters.q &&
    existing.filters.pageSize === page.filters.pageSize

  if (!sameQuery) {
    return { ids: page.ids, page: page.page, totalPages: page.totalPages, filters: page.filters }
  }

  if (page.page === existing.page) {
    return { ...existing, ids: page.ids, totalPages: page.totalPages }
  }
  if (page.page === existing.page + 1) {
    return {
      ids: [...existing.ids, ...page.ids],
      page: page.page,
      totalPages: page.totalPages,
      filters: page.filters,
    }
  }
  if (page.page === existing.page - 1) {
    return {
      ids: [...page.ids, ...existing.ids],
      page: page.page,
      totalPages: page.totalPages,
      filters: page.filters,
    }
  }
  // A non-adjacent page (e.g. the telecaller jumped via the pagination
  // bar) — start a fresh sequence rather than splicing a gap into the
  // middle of the old one.
  return { ids: page.ids, page: page.page, totalPages: page.totalPages, filters: page.filters }
}

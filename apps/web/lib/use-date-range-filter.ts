"use client"

import * as React from "react"

import type { DateRangeValue } from "@/components/shared/date-range-picker"
import {
  resolveDashboardDateRangePreset,
  type DashboardDateRangePreset,
} from "@/lib/ist-date"
import { useUrlFilters } from "@/lib/use-url-filters"

const URL_DEFAULTS = { date_from: "", date_to: "" }

/** One date-range filter for a dashboard/report page, shared by the
 * Shipment Tracking, Fulfilment & Delivery Performance and Shipping
 * Partner Performance pages so they all behave identically:
 *
 * - the selected range lives in the URL (`date_from`/`date_to`), so it
 *   survives a refresh and is kept when other filters change;
 * - with nothing selected it falls back to an IST-bounded default preset
 *   (Last 30 Days), so every card, table, drilldown and export on the
 *   page queries the *same* explicit window (the backend's own defaults
 *   differ per endpoint);
 * - `dateFrom`/`dateTo` are the ISO instants to send to the API --
 *   always IST day boundaries via `lib/ist-date.ts`.
 */
export function useDateRangeFilter(
  defaultPreset: DashboardDateRangePreset = "last_30_days"
) {
  const { filters, setFilters } = useUrlFilters(URL_DEFAULTS)

  const defaultRange = React.useMemo(
    () => resolveDashboardDateRangePreset(defaultPreset, new Date()),
    [defaultPreset]
  )

  const from = filters.date_from ? new Date(filters.date_from) : defaultRange.from
  const to = filters.date_to ? new Date(filters.date_to) : defaultRange.to
  const range: DateRangeValue = { from, to }

  const setRange = React.useCallback(
    (next: DateRangeValue) =>
      setFilters({
        date_from: next.from ? next.from.toISOString() : "",
        date_to: next.to ? next.to.toISOString() : "",
      }),
    [setFilters]
  )

  return {
    range,
    setRange,
    dateFrom: from.toISOString(),
    dateTo: to.toISOString(),
    /** True while the viewer hasn't picked a range (the default is active). */
    isDefaultRange: !filters.date_from && !filters.date_to,
  }
}

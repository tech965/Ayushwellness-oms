"use client"

import * as React from "react"
import { PackageCheck, PackageX } from "lucide-react"
import { endOfDay, startOfDay, subDays } from "date-fns"

import { DrilldownOrdersTable } from "@/components/analytics/drilldown-orders-table"
import { PageHeader } from "@/components/shared/page-header"
import { StatTile } from "@/components/shared/stat-tile"
import { Skeleton } from "@/components/ui/skeleton"
import { formatDate } from "@/lib/format"
import { useUrlFilters } from "@/lib/use-url-filters"
import { useOrders } from "@/services/orders"

const FILTER_DEFAULTS = { date_from: "", date_to: "", fulfillment: "" }

/** Pending COD's Fulfilled/Unfulfilled breakdown -- the one new drill-down
 * step this request adds (Pending COD -> Fulfilled/Unfulfilled -> orders).
 * Counts are read straight off the existing, RBAC-gated `/orders` list
 * endpoint (two `pageSize: 1` calls, reading `meta.total_items`) rather
 * than a new analytics endpoint, since that's the minimum change needed
 * and reuses exactly the same data every other drill-down/Orders view
 * already relies on.
 */
export function CodPendingFulfillmentContent() {
  const { filters } = useUrlFilters(FILTER_DEFAULTS)

  const resolvedFrom = filters.date_from
    ? new Date(filters.date_from)
    : startOfDay(subDays(new Date(), 29))
  const resolvedTo = filters.date_to ? new Date(filters.date_to) : endOfDay(new Date())
  const dateParams = { date_from: resolvedFrom.toISOString(), date_to: resolvedTo.toISOString() }
  const orderQueryString = new URLSearchParams(dateParams).toString()

  const baseFilters = { ...dateParams, payment_type: "cod" as const, payment_status: "pending" as const }

  function fulfillmentHref(status: "fulfilled" | "unfulfilled"): string {
    const params = new URLSearchParams(orderQueryString)
    params.set("fulfillment", status)
    return `/revenue/cod/pending?${params.toString()}`
  }

  function ordersHref(fulfillmentStatus: "fulfilled" | "unfulfilled"): string {
    const params = new URLSearchParams(orderQueryString)
    params.set("payment_type", "cod")
    params.set("payment_status", "pending")
    params.set("fulfillment_status", fulfillmentStatus)
    return `/orders?${params.toString()}`
  }

  const fulfilledQuery = useOrders({
    page: 1,
    pageSize: 1,
    ...baseFilters,
    fulfillment_status: "fulfilled",
  })
  const unfulfilledQuery = useOrders({
    page: 1,
    pageSize: 1,
    ...baseFilters,
    fulfillment_status: "unfulfilled",
  })

  const isLoading = fulfilledQuery.isLoading || unfulfilledQuery.isLoading
  const fulfilledCount = fulfilledQuery.data?.meta.total_items ?? 0
  const unfulfilledCount = unfulfilledQuery.data?.meta.total_items ?? 0

  const selected =
    filters.fulfillment === "fulfilled" || filters.fulfillment === "unfulfilled"
      ? filters.fulfillment
      : null

  return (
    <>
      <PageHeader
        title="Pending COD — Fulfillment Status"
        description={`${formatDate(resolvedFrom.toISOString())} – ${formatDate(resolvedTo.toISOString())}`}
        backHref={`/revenue/cod?${orderQueryString}`}
        backLabel="Back to COD Revenue"
      />

      {isLoading ? (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          {Array.from({ length: 2 }).map((_, i) => (
            <Skeleton key={i} className="h-28 w-full" />
          ))}
        </div>
      ) : (
        <div className="flex flex-col gap-6">
          <section className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <StatTile
              label="Fulfilled"
              icon={PackageCheck}
              value={fulfilledCount.toLocaleString("en-IN")}
              accent="emerald"
              href={fulfillmentHref("fulfilled")}
            />
            <StatTile
              label="Unfulfilled"
              icon={PackageX}
              value={unfulfilledCount.toLocaleString("en-IN")}
              accent="amber"
              href={fulfillmentHref("unfulfilled")}
            />
          </section>

          {selected && (
            <DrilldownOrdersTable
              title={selected === "fulfilled" ? "Fulfilled Pending COD Orders" : "Unfulfilled Pending COD Orders"}
              filters={{ ...baseFilters, fulfillment_status: selected }}
              ordersHref={ordersHref(selected)}
              hidePaymentColumn
            />
          )}
        </div>
      )}
    </>
  )
}

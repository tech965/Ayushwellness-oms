"use client"

import * as React from "react"
import { endOfDay, startOfDay, subDays } from "date-fns"

import { FulfillmentPaymentOrdersTable } from "@/components/analytics/fulfillment-payment-orders-table"
import { PageHeader } from "@/components/shared/page-header"
import { QueryStates } from "@/components/shared/query-states"
import { SelectableStatCard } from "@/components/shared/selectable-stat-card"
import { Skeleton } from "@/components/ui/skeleton"
import { formatDate, formatMoney } from "@/lib/format"
import { useUrlFilters } from "@/lib/use-url-filters"
import { useCodDashboard } from "@/services/analytics"

const FILTER_DEFAULTS = { date_from: "", date_to: "" }

type CodKpi = "total" | "fulfilled" | "unfulfilled" | "delivered"

/** COD Orders dashboard (requirement 2): 4 clickable KPIs -- Total COD,
 * Fulfilled COD, Unfulfilled COD, Delivered COD -- each with amount,
 * order count and %, each expanding into its matching order list below.
 * `Total COD`/`Fulfilled COD`/`Unfulfilled COD` use the Shopify
 * `fulfillment_status` axis (same definition as the Order Breakdown
 * page's Fulfilled/Unfulfilled panels); `Delivered COD` uses the
 * DIFFERENT, Module-2 shipment-status axis -- see
 * `AnalyticsService.get_cod_dashboard`'s docstring. The two axes are not
 * mutually exclusive and are not expected to sum together.
 */
export function CodDashboardContent() {
  const { filters } = useUrlFilters(FILTER_DEFAULTS)
  const [selectedKpi, setSelectedKpi] = React.useState<CodKpi | null>(null)

  const resolvedFrom = filters.date_from
    ? new Date(filters.date_from)
    : startOfDay(subDays(new Date(), 29))
  const resolvedTo = filters.date_to ? new Date(filters.date_to) : endOfDay(new Date())
  const dateFrom = resolvedFrom.toISOString()
  const dateTo = resolvedTo.toISOString()

  const query = useCodDashboard({ date_from: dateFrom, date_to: dateTo })

  function toggleKpi(kpi: CodKpi) {
    setSelectedKpi((current) => (current === kpi ? null : kpi))
  }

  return (
    <>
      <PageHeader
        title="COD Orders Dashboard"
        description={`${formatDate(dateFrom)} – ${formatDate(dateTo)}`}
        backHref="/orders/breakdown"
        backLabel="Back to Order Breakdown"
      />
      <QueryStates
        isLoading={query.isLoading}
        isError={query.isError}
        error={query.error}
        data={query.data}
        onRetry={() => query.refetch()}
      >
        {(data) => (
          <div className="flex flex-col gap-6">
            <section className="grid grid-cols-2 gap-4 lg:grid-cols-4">
              <SelectableStatCard
                label="Total COD"
                value={formatMoney(data.total_cod.amount)}
                detail={`${data.total_cod.order_count.toLocaleString("en-IN")} orders · ${data.total_cod.percent_of_total_cod_amount.toFixed(1)}%`}
                selected={selectedKpi === "total"}
                onClick={() => toggleKpi("total")}
                accent="blue"
              />
              <SelectableStatCard
                label="Fulfilled COD"
                value={formatMoney(data.fulfilled_cod.amount)}
                detail={`${data.fulfilled_cod.order_count.toLocaleString("en-IN")} orders · ${data.fulfilled_cod.percent_of_total_cod_amount.toFixed(1)}% of Total COD`}
                selected={selectedKpi === "fulfilled"}
                onClick={() => toggleKpi("fulfilled")}
                accent="green"
              />
              <SelectableStatCard
                label="Unfulfilled COD"
                value={formatMoney(data.unfulfilled_cod.amount)}
                detail={`${data.unfulfilled_cod.order_count.toLocaleString("en-IN")} orders · ${data.unfulfilled_cod.percent_of_total_cod_amount.toFixed(1)}% of Total COD`}
                selected={selectedKpi === "unfulfilled"}
                onClick={() => toggleKpi("unfulfilled")}
                accent="amber"
              />
              <SelectableStatCard
                label="Delivered COD"
                value={formatMoney(data.delivered_cod.amount)}
                detail={`${data.delivered_cod.order_count.toLocaleString("en-IN")} orders · ${data.delivered_cod.percent_of_total_cod_amount.toFixed(1)}% of Total COD`}
                selected={selectedKpi === "delivered"}
                onClick={() => toggleKpi("delivered")}
                accent="violet"
              />
            </section>

            {selectedKpi && (
              <FulfillmentPaymentOrdersTable
                key={selectedKpi}
                title={
                  selectedKpi === "total"
                    ? "All COD Orders"
                    : selectedKpi === "fulfilled"
                      ? "Fulfilled COD Orders"
                      : selectedKpi === "unfulfilled"
                        ? "Unfulfilled COD Orders"
                        : "Delivered COD Orders"
                }
                dateFrom={dateFrom}
                dateTo={dateTo}
                paymentType="cod"
                fulfillmentStatus={
                  selectedKpi === "fulfilled"
                    ? "fulfilled"
                    : selectedKpi === "unfulfilled"
                      ? "unfulfilled"
                      : undefined
                }
                unifiedStatus={selectedKpi === "delivered" ? "delivered" : undefined}
              />
            )}
          </div>
        )}
      </QueryStates>
    </>
  )
}

export function CodDashboardSkeleton() {
  return (
    <>
      <PageHeader title="COD Orders Dashboard" backHref="/orders/breakdown" backLabel="Back to Order Breakdown" />
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        {Array.from({ length: 4 }).map((_, i) => (
          <Skeleton key={i} className="h-28 w-full" />
        ))}
      </div>
    </>
  )
}

"use client"

import * as React from "react"

import { DateRangePicker, type DateRangeValue } from "@/components/shared/date-range-picker"
import { PageHeader } from "@/components/shared/page-header"
import { QueryStates } from "@/components/shared/query-states"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { useUrlFilters } from "@/lib/use-url-filters"
import { useMarketplaceDashboardSummary } from "@/services/marketplace"
import { MARKETPLACE_OPTIONS } from "@/types/marketplace"

const FILTER_DEFAULTS = { date_from: "", date_to: "" }

export default function MarketplaceReportsPage() {
  return (
    <React.Suspense fallback={null}>
      <MarketplaceReportsContent />
    </React.Suspense>
  )
}

function MarketplaceReportsContent() {
  const { filters, setFilters } = useUrlFilters(FILTER_DEFAULTS)

  const dateRange: DateRangeValue = {
    from: filters.date_from ? new Date(filters.date_from) : undefined,
    to: filters.date_to ? new Date(filters.date_to) : undefined,
  }

  const query = useMarketplaceDashboardSummary({
    date_from: filters.date_from || undefined,
    date_to: filters.date_to || undefined,
  })

  return (
    <>
      <PageHeader
        title="Marketplace Reports"
        description="Order volume comparison across Amazon, Flipkart, and Meesho for the selected date range."
        actions={
          <DateRangePicker
            value={dateRange}
            onChange={(range) =>
              setFilters({
                date_from: range.from ? range.from.toISOString() : "",
                date_to: range.to ? range.to.toISOString() : "",
              })
            }
          />
        }
      />
      <QueryStates
        isLoading={query.isLoading}
        isError={query.isError}
        error={query.error}
        data={query.data}
        onRetry={() => void query.refetch()}
      >
        {(data) => {
          const perMarketplace: Record<string, string> = {
            amazon: data.amazon_orders.current,
            flipkart: data.flipkart_orders.current,
            meesho: data.meesho_orders.current,
          }
          const total = Number(data.total_orders.current) || 0

          return (
            <Card>
              <CardHeader>
                <CardTitle>Marketplace-wise Order Comparison</CardTitle>
              </CardHeader>
              <CardContent>
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-muted-foreground border-border border-b text-left">
                      <th className="py-2 font-medium">Marketplace</th>
                      <th className="py-2 text-right font-medium">Orders</th>
                      <th className="py-2 text-right font-medium">% of Total</th>
                    </tr>
                  </thead>
                  <tbody>
                    {MARKETPLACE_OPTIONS.map((marketplace) => {
                      const value = Number(perMarketplace[marketplace.value]) || 0
                      const pct = total > 0 ? ((value / total) * 100).toFixed(1) : "0.0"
                      return (
                        <tr key={marketplace.value} className="border-border border-b last:border-0">
                          <td className="py-2">{marketplace.label}</td>
                          <td className="py-2 text-right">{value.toLocaleString("en-IN")}</td>
                          <td className="py-2 text-right">{pct}%</td>
                        </tr>
                      )
                    })}
                    <tr className="font-medium">
                      <td className="py-2">Total</td>
                      <td className="py-2 text-right">{total.toLocaleString("en-IN")}</td>
                      <td className="py-2 text-right">100%</td>
                    </tr>
                  </tbody>
                </table>
              </CardContent>
            </Card>
          )
        }}
      </QueryStates>
    </>
  )
}

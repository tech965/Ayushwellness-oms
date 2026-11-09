"use client"

import * as React from "react"
import { IndianRupee, PackageCheck, ShoppingCart, UserCheck, Users, XCircle } from "lucide-react"

import { DateRangePicker, type DateRangeValue } from "@/components/shared/date-range-picker"
import { LiveResultNotice } from "@/components/shared/live-result-notice"
import { PageHeader } from "@/components/shared/page-header"
import { StatTile } from "@/components/shared/stat-tile"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { getApiErrorMessage } from "@/lib/api-client"
import { formatMoney } from "@/lib/format"
import { useLiveAnalyticsSummary } from "@/services/live"
import type { LiveLabelCount } from "@/types/live"

function titleCase(label: string): string {
  return label.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase())
}

function BreakdownCard({ title, rows }: { title: string; rows: LiveLabelCount[] }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm font-medium">{title}</CardTitle>
      </CardHeader>
      <CardContent>
        {rows.length === 0 ? (
          <p className="text-sm text-muted-foreground">No orders in this range.</p>
        ) : (
          <ul className="flex flex-col gap-2 text-sm">
            {rows.map((row) => (
              <li key={row.label} className="flex items-center justify-between">
                <span>{titleCase(row.label)}</span>
                <span className="tabular-nums">
                  {row.count.toLocaleString("en-IN")}
                  <span className="ml-2 text-muted-foreground">{formatMoney(row.amount)}</span>
                </span>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  )
}

/** Dashboard shown while the deployment reads provider data live. Every
 * figure is computed from a bounded live scan; when the scan stopped
 * early the page says so, and says which numbers are not available at
 * all, rather than showing zeros. */
export function LiveDashboard() {
  const [range, setRange] = React.useState<DateRangeValue>({ from: undefined, to: undefined })
  const params = React.useMemo(
    () => ({
      date_from: range.from ? range.from.toISOString() : undefined,
      date_to: range.to ? range.to.toISOString() : undefined,
    }),
    [range]
  )
  const query = useLiveAnalyticsSummary(params)
  const data = query.data

  return (
    <>
      <PageHeader
        title="Dashboard"
        description="Read live from Shopify. Nothing on this page is stored."
        actions={<DateRangePicker value={range} onChange={setRange} />}
      />
      <div className="flex flex-col gap-6" data-testid="live-dashboard">
        {query.isError ? (
          <Alert variant="destructive">
            <AlertTitle>Could not load live figures</AlertTitle>
            <AlertDescription>{getApiErrorMessage(query.error)}</AlertDescription>
          </Alert>
        ) : null}

        {query.isLoading || !data ? (
          query.isError ? null : <Skeleton className="h-40 w-full" />
        ) : (
          <>
            <LiveResultNotice
              meta={data.complete ? undefined : { warnings: data.warnings }}
            />
            <p className="text-sm text-muted-foreground">
              Based on {data.orders_scanned.toLocaleString("en-IN")} orders
              {data.complete ? "." : " (partial scan — not a global total)."}
            </p>

            <div className="grid grid-cols-2 gap-4 lg:grid-cols-3 xl:grid-cols-6">
              <StatTile label="Total orders" value={data.total_orders} icon={ShoppingCart} accent="blue" />
              <StatTile
                label="Revenue"
                value={formatMoney(data.revenue)}
                subtext={`Avg ${formatMoney(data.average_order_value)}`}
                icon={IndianRupee}
                accent="emerald"
              />
              <StatTile label="Cancelled" value={data.cancelled_orders} icon={XCircle} accent="orange" />
              <StatTile label="Confirmed" value={data.confirmed_orders} icon={PackageCheck} accent="violet" />
              <StatTile label="Assigned" value={data.assigned_orders} icon={UserCheck} accent="amber" />
              <StatTile label="Unassigned" value={data.unassigned_orders} icon={Users} accent="slate" />
            </div>

            <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
              <BreakdownCard title="Fulfilment" rows={data.by_fulfillment_status} />
              <BreakdownCard title="Payment type" rows={data.by_payment_type} />
              <BreakdownCard title="Payment status" rows={data.by_payment_status} />
              <BreakdownCard title="Delivery (Shopify)" rows={data.by_shopify_delivery_status} />
            </div>

            <Card>
              <CardHeader>
                <CardTitle className="text-sm font-medium">Top products</CardTitle>
              </CardHeader>
              <CardContent>
                {data.top_products.length === 0 ? (
                  <p className="text-sm text-muted-foreground">No products in this range.</p>
                ) : (
                  <ul className="flex flex-col gap-2 text-sm">
                    {data.top_products.map((product) => (
                      <li key={`${product.sku}-${product.product_name}`} className="flex justify-between">
                        <span>{product.product_name}</span>
                        <span className="tabular-nums">
                          {product.quantity.toLocaleString("en-IN")} ·{" "}
                          {formatMoney(product.revenue)}
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
              </CardContent>
            </Card>

            {data.unavailable.length > 0 ? (
              <Alert data-testid="live-unavailable">
                <AlertTitle>Not shown yet</AlertTitle>
                <AlertDescription>
                  {data.unavailable.map((note) => (
                    <p key={note}>{note}</p>
                  ))}
                </AlertDescription>
              </Alert>
            ) : null}
          </>
        )}
      </div>
    </>
  )
}

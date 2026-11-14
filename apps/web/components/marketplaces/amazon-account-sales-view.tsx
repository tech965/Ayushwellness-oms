"use client"

import * as React from "react"
import { IndianRupee, ShoppingBag } from "lucide-react"

import { AmazonOrderDrilldown } from "@/components/marketplaces/amazon-order-drilldown"
import { StatTile } from "@/components/shared/stat-tile"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import type { AccountSalesReport, PeriodPoint } from "@/types/amazon-sales"

import { money } from "./amazon-sales-view"

function PeriodTable({
  rows,
  currency,
  onDateClick,
}: {
  rows: PeriodPoint[]
  currency: string | null
  /** Only passed for the daily tab -- see amazon-sales-view.tsx's
   * PeriodTable for why weekly/monthly rows aren't clickable. */
  onDateClick?: (date: string) => void
}) {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Period</TableHead>
          <TableHead className="text-right">Units ordered</TableHead>
          <TableHead className="text-right">Ordered sales</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.length === 0 && (
          <TableRow>
            <TableCell colSpan={3} className="text-muted-foreground text-center">
              No data for this range.
            </TableCell>
          </TableRow>
        )}
        {rows.map((r) => (
          <TableRow
            key={r.period}
            className={onDateClick ? "cursor-pointer" : undefined}
            onClick={onDateClick ? () => onDateClick(r.period) : undefined}
          >
            <TableCell>{r.label}</TableCell>
            <TableCell className="text-right">{r.units_ordered}</TableCell>
            <TableCell className="text-right">{money(r.ordered_revenue, currency)}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}

/** Account-level sales (all products combined) -- from the Sales API,
 * works independently of the Reports API. This view intentionally has no
 * product/ASIN/SKU column: that data does not exist at this level and
 * must never be fabricated. */
export function AmazonAccountSalesView({ report }: { report: AccountSalesReport }) {
  const [drilldownDate, setDrilldownDate] = React.useState<string | null>(null)
  const cur = report.summary.currency
  const s = report.summary

  return (
    <div className="space-y-4">
      <Alert>
        <AlertTitle>Account-level metrics -- not product-wise</AlertTitle>
        <AlertDescription>{report.scope_note}</AlertDescription>
      </Alert>

      <div className="grid gap-4 sm:grid-cols-2">
        <StatTile
          label="Total Units Ordered (account-level)"
          value={s.total_units_ordered}
          icon={ShoppingBag}
        />
        <StatTile
          label="Total Ordered Sales (account-level)"
          value={money(s.total_ordered_revenue, cur)}
          icon={IndianRupee}
          subtext="Sales revenue, not profit. All products combined."
        />
      </div>

      <Tabs defaultValue="daily">
        <TabsList>
          <TabsTrigger value="daily">Daily</TabsTrigger>
          <TabsTrigger value="weekly">Weekly</TabsTrigger>
          <TabsTrigger value="monthly">Monthly</TabsTrigger>
        </TabsList>
        <TabsContent value="daily">
          <PeriodTable
            currency={cur}
            rows={report.daily.map((d) => ({
              period: d.date,
              label: d.date,
              units_ordered: d.units_ordered,
              ordered_revenue: d.ordered_revenue,
              units_shipped: null,
              shipped_revenue: null,
              units_refunded: null,
            }))}
            onDateClick={setDrilldownDate}
          />
        </TabsContent>
        <TabsContent value="weekly">
          <PeriodTable rows={report.weekly} currency={cur} />
        </TabsContent>
        <TabsContent value="monthly">
          <PeriodTable rows={report.monthly} currency={cur} />
        </TabsContent>
      </Tabs>

      <AmazonOrderDrilldown
        date={drilldownDate}
        onOpenChange={(open) => !open && setDrilldownDate(null)}
      />
    </div>
  )
}

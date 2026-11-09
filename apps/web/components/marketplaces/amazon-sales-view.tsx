"use client"

import * as React from "react"
import { IndianRupee, Package, ShoppingBag, TrendingUp } from "lucide-react"
import {
  Bar,
  BarChart,
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts"

import { StatTile } from "@/components/shared/stat-tile"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { formatMoney } from "@/lib/format"
import type { PeriodPoint, SalesReport } from "@/types/amazon-sales"

export const UNAVAILABLE = "Unavailable"

export function money(v: string | null | undefined, currency: string | null): string {
  return v == null ? UNAVAILABLE : formatMoney(v, currency ?? "INR")
}

export function num(v: number | null | undefined): string {
  return v == null ? UNAVAILABLE : String(v)
}

function PeriodTable({ rows, currency }: { rows: PeriodPoint[]; currency: string | null }) {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Period</TableHead>
          <TableHead className="text-right">Units ordered</TableHead>
          <TableHead className="text-right">Ordered sales</TableHead>
          <TableHead className="text-right">Units shipped</TableHead>
          <TableHead className="text-right">Shipped sales</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map((r) => (
          <TableRow key={r.period}>
            <TableCell>{r.label}</TableCell>
            <TableCell className="text-right">{r.units_ordered}</TableCell>
            <TableCell className="text-right">{money(r.ordered_revenue, currency)}</TableCell>
            <TableCell className="text-right">{num(r.units_shipped)}</TableCell>
            <TableCell className="text-right">{money(r.shipped_revenue, currency)}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}

export function AmazonSalesView({ report }: { report: SalesReport }) {
  const cur = report.summary.currency
  const s = report.summary
  const trend = report.daily.map((d) => ({
    date: d.date,
    revenue: Number(d.ordered_revenue),
    units: d.units_ordered,
  }))
  const top = report.products.slice(0, 10).map((p) => ({
    name: p.sku ?? p.asin,
    revenue: Number(p.ordered_revenue),
  }))

  return (
    <div className="space-y-6">
      {report.warnings.map((w) => (
        <Alert key={w}>
          <AlertTitle>Note</AlertTitle>
          <AlertDescription>{w}</AlertDescription>
        </Alert>
      ))}

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatTile label="Total Units Ordered" value={s.total_units_ordered} icon={ShoppingBag} />
        <StatTile
          label="Ordered Product Sales"
          value={money(s.total_ordered_revenue, cur)}
          icon={IndianRupee}
          subtext="Sales revenue, not profit"
        />
        <StatTile label="Products Sold" value={s.products_sold} icon={Package} />
        <StatTile
          label="Shipped Product Sales"
          value={money(s.total_shipped_revenue, cur)}
          icon={TrendingUp}
          subtext={`Units shipped: ${num(s.total_units_shipped)}`}
        />
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="text-sm">Daily sales revenue (ordered)</CardTitle>
          </CardHeader>
          <CardContent className="h-64">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={trend}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="date" fontSize={11} />
                <YAxis fontSize={11} />
                <Tooltip />
                <Line type="monotone" dataKey="revenue" name="Ordered sales" stroke="#2563eb" dot={false} />
              </LineChart>
            </ResponsiveContainer>
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle className="text-sm">Daily units ordered</CardTitle>
          </CardHeader>
          <CardContent className="h-64">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={trend}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="date" fontSize={11} />
                <YAxis fontSize={11} />
                <Tooltip />
                <Bar dataKey="units" name="Units" fill="#16a34a" />
              </BarChart>
            </ResponsiveContainer>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Product-wise revenue comparison (top 10)</CardTitle>
        </CardHeader>
        <CardContent className="h-72">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={top} layout="vertical" margin={{ left: 40 }}>
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis type="number" fontSize={11} />
              <YAxis type="category" dataKey="name" fontSize={11} width={100} />
              <Tooltip />
              <Bar dataKey="revenue" name="Ordered sales" fill="#7c3aed" />
            </BarChart>
          </ResponsiveContainer>
        </CardContent>
      </Card>

      <Tabs defaultValue="products">
        <TabsList>
          <TabsTrigger value="products">Products</TabsTrigger>
          <TabsTrigger value="daily">Daily report</TabsTrigger>
          <TabsTrigger value="weekly">Weekly report</TabsTrigger>
          <TabsTrigger value="monthly">Monthly report</TabsTrigger>
        </TabsList>
        <TabsContent value="products">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Product</TableHead>
                <TableHead>Seller SKU</TableHead>
                <TableHead>ASIN</TableHead>
                <TableHead className="text-right">Units ordered</TableHead>
                <TableHead className="text-right">Units shipped</TableHead>
                <TableHead className="text-right">Ordered sales</TableHead>
                <TableHead className="text-right">Avg selling price</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {report.products.length === 0 && (
                <TableRow>
                  <TableCell colSpan={7} className="text-muted-foreground text-center">
                    No products match.
                  </TableCell>
                </TableRow>
              )}
              {report.products.map((p) => (
                <TableRow key={`${p.asin}-${p.sku}`}>
                  <TableCell>{p.product_name ?? UNAVAILABLE}</TableCell>
                  <TableCell>{p.sku ?? UNAVAILABLE}</TableCell>
                  <TableCell>{p.asin}</TableCell>
                  <TableCell className="text-right">{p.units_ordered}</TableCell>
                  <TableCell className="text-right">{num(p.units_shipped)}</TableCell>
                  <TableCell className="text-right">{money(p.ordered_revenue, cur)}</TableCell>
                  <TableCell className="text-right">{money(p.avg_selling_price, cur)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TabsContent>
        <TabsContent value="daily">
          <PeriodTable
            currency={cur}
            rows={report.daily.map((d) => ({ ...d, period: d.date, label: d.date }))}
          />
        </TabsContent>
        <TabsContent value="weekly">
          <PeriodTable rows={report.weekly} currency={cur} />
        </TabsContent>
        <TabsContent value="monthly">
          <PeriodTable rows={report.monthly} currency={cur} />
        </TabsContent>
      </Tabs>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Metric definitions</CardTitle>
        </CardHeader>
        <CardContent className="text-muted-foreground space-y-1 text-xs">
          {Object.entries(report.definitions).map(([k, v]) => (
            <p key={k}>
              <strong>{k}:</strong> {v}
            </p>
          ))}
          {Object.entries(report.unavailable).map(([k, v]) => (
            <p key={k}>
              <strong>Unavailable – {k}:</strong> {v}
            </p>
          ))}
        </CardContent>
      </Card>
    </div>
  )
}

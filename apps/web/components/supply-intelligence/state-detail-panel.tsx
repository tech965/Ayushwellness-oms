"use client"

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { StatTile } from "@/components/shared/stat-tile"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { formatMoney } from "@/lib/format"
import {
  Boxes,
  Clock,
  IndianRupee,
  PackageCheck,
  RotateCcw,
  ShoppingCart,
  Truck,
  Users,
} from "lucide-react"
import type { StateDetail } from "@/types/supply-intelligence"

interface StateDetailPanelProps {
  state: string
  detail: StateDetail | undefined
  isLoading: boolean
}

export function StateDetailPanel({ state, detail, isLoading }: StateDetailPanelProps) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>{state}</CardTitle>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <Skeleton className="h-64 w-full" />
        ) : !detail ? (
          <p className="text-muted-foreground text-sm">
            No order data available for {state} in this period.
          </p>
        ) : (
          <div className="flex flex-col gap-6">
            <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
              <StatTile label="Orders" value={detail.orders.toLocaleString("en-IN")} icon={ShoppingCart} accent="emerald" />
              <StatTile label="Revenue" value={formatMoney(detail.revenue)} icon={IndianRupee} accent="emerald" />
              <StatTile label="Avg. Order Value" value={formatMoney(detail.avg_order_value)} icon={IndianRupee} accent="blue" />
              <StatTile label="Customers" value={detail.customers.toLocaleString("en-IN")} icon={Users} accent="violet" />
              <StatTile label="Delivered" value={detail.delivered.toLocaleString("en-IN")} icon={PackageCheck} accent="emerald" />
              <StatTile label="In Transit" value={detail.in_transit.toLocaleString("en-IN")} icon={Truck} accent="blue" />
              <StatTile label="Pending" value={detail.pending.toLocaleString("en-IN")} icon={Clock} accent="orange" />
              <StatTile
                label="RTO Rate"
                value={detail.rto_rate_pct === null ? "—" : `${detail.rto_rate_pct.toFixed(1)}%`}
                icon={RotateCcw}
                accent="amber"
                subtext={`${detail.rto.toLocaleString("en-IN")} RTO · ${detail.ndr.toLocaleString("en-IN")} NDR`}
              />
            </div>

            <Tabs defaultValue="cities">
              <TabsList>
                <TabsTrigger value="cities">Top Cities</TabsTrigger>
                <TabsTrigger value="products">Top Products</TabsTrigger>
              </TabsList>
              <TabsContent value="cities" className="pt-3">
                {detail.cities.length === 0 ? (
                  <p className="text-muted-foreground text-sm">No city data available.</p>
                ) : (
                  <ul className="flex flex-col gap-2">
                    {detail.cities.map((city) => (
                      <li key={city.city} className="flex items-center justify-between text-sm">
                        <span className="flex items-center gap-2">
                          <Boxes className="text-muted-foreground size-3.5" />
                          {city.city}
                        </span>
                        <span className="flex items-center gap-3 tabular-nums">
                          <span>{city.orders.toLocaleString("en-IN")} orders</span>
                          <span className="text-muted-foreground">{formatMoney(city.revenue)}</span>
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
              </TabsContent>
              <TabsContent value="products" className="pt-3">
                {detail.products.length === 0 ? (
                  <p className="text-muted-foreground text-sm">No product data available.</p>
                ) : (
                  <ul className="flex flex-col gap-2">
                    {detail.products.map((product) => (
                      <li key={product.sku} className="flex items-center justify-between text-sm">
                        <span>{product.product_name}</span>
                        <span className="flex items-center gap-3 tabular-nums">
                          <span>{product.quantity.toLocaleString("en-IN")} units</span>
                          <span>{product.orders.toLocaleString("en-IN")} orders</span>
                          <span className="text-muted-foreground">{formatMoney(product.revenue)}</span>
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
              </TabsContent>
            </Tabs>

            <p className="text-muted-foreground text-xs">
              Shipment metrics are based on shipments successfully matched to OMS orders.
            </p>
          </div>
        )}
      </CardContent>
    </Card>
  )
}

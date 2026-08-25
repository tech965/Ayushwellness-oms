"use client"

import {
  AlertTriangle,
  Boxes,
  ClipboardList,
  PackageCheck,
  RotateCcw,
  ShoppingCart,
  Truck,
  Users,
} from "lucide-react"

import { PageHeader } from "@/components/shared/page-header"
import { QueryStates } from "@/components/shared/query-states"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { useDashboardSummary } from "@/services/dashboard"
import type { DashboardSummary } from "@/types/dashboard"

const STAT_CONFIG: {
  key: keyof DashboardSummary
  label: string
  icon: typeof ShoppingCart
}[] = [
  { key: "total_orders", label: "Total orders", icon: ShoppingCart },
  { key: "total_customers", label: "Total customers", icon: Users },
  { key: "total_products", label: "Total products", icon: Boxes },
  { key: "total_shipments", label: "Total shipments", icon: Truck },
  { key: "delivered_shipments", label: "Delivered shipments", icon: PackageCheck },
  { key: "delayed_shipments", label: "Delayed shipments", icon: AlertTriangle },
  { key: "open_ndr_count", label: "Open NDR", icon: ClipboardList },
  { key: "open_rto_count", label: "Open RTO", icon: RotateCcw },
]

export default function DashboardPage() {
  const query = useDashboardSummary()

  return (
    <>
      <PageHeader title="Dashboard" description="Live counts from the OMS database." />
      <QueryStates
        isLoading={query.isLoading}
        isError={query.isError}
        error={query.error}
        data={query.data}
        onRetry={() => void query.refetch()}
      >
        {(summary) => (
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4">
            {STAT_CONFIG.map(({ key, label, icon: Icon }) => (
              <Card key={key}>
                <CardHeader className="flex flex-row items-center justify-between gap-2 pb-2">
                  <CardTitle className="text-muted-foreground text-sm font-medium">
                    {label}
                  </CardTitle>
                  <Icon className="text-muted-foreground size-4" />
                </CardHeader>
                <CardContent>
                  <p className="text-2xl font-semibold">{summary[key]}</p>
                </CardContent>
              </Card>
            ))}
          </div>
        )}
      </QueryStates>
    </>
  )
}

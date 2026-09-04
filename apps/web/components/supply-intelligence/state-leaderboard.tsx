"use client"

import * as React from "react"

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { DataTable, type DataTableColumn } from "@/components/shared/data-table"
import { formatMoney } from "@/lib/format"
import { OpportunityBadge } from "./opportunity-badge"
import type { StateMetric } from "@/types/supply-intelligence"

type SortKey = "orders" | "revenue" | "rto_rate_pct" | "growth_pct"

interface StateLeaderboardProps {
  states: StateMetric[]
  selectedState: string | null
  onSelectState: (state: string) => void
}

function GrowthCell({ value }: { value: number | null }) {
  if (value === null) return <span className="text-muted-foreground">—</span>
  const tone = value > 0 ? "text-emerald-600 dark:text-emerald-400" : value < 0 ? "text-red-600 dark:text-red-400" : "text-muted-foreground"
  return (
    <span className={tone}>
      {value > 0 ? "+" : ""}
      {value.toFixed(1)}%
    </span>
  )
}

/** Ranked "Top States" table (spec section 4). States with zero orders
 * (the majority of the canonical 36-state list on a real dataset) are
 * excluded here -- an all-zero leaderboard isn't useful; those states
 * still appear on the map and in "Untapped Markets" instead.
 */
export function StateLeaderboard({
  states,
  selectedState,
  onSelectState,
}: StateLeaderboardProps) {
  const [sortKey, setSortKey] = React.useState<SortKey>("orders")
  const [sortOrder, setSortOrder] = React.useState<"asc" | "desc">("desc")

  const activeStates = states.filter((s) => s.orders > 0)
  const sorted = [...activeStates].sort((a, b) => {
    const aValue = sortKey === "revenue" ? Number(a.revenue) : (a[sortKey] ?? -Infinity)
    const bValue = sortKey === "revenue" ? Number(b.revenue) : (b[sortKey] ?? -Infinity)
    return sortOrder === "asc" ? aValue - bValue : bValue - aValue
  })

  function handleSortChange(key: string) {
    if (key === sortKey) {
      setSortOrder(sortOrder === "asc" ? "desc" : "asc")
    } else {
      setSortKey(key as SortKey)
      setSortOrder("desc")
    }
  }

  const columns: DataTableColumn<StateMetric>[] = [
    {
      id: "rank",
      header: "#",
      cell: (row) => sorted.indexOf(row) + 1,
      className: "w-10 text-muted-foreground",
    },
    { id: "state", header: "State", cell: (row) => <span className="font-medium">{row.state}</span> },
    {
      id: "orders",
      header: "Orders",
      cell: (row) => row.orders.toLocaleString("en-IN"),
      className: "text-right",
      sortKey: "orders",
    },
    {
      id: "revenue",
      header: "Revenue",
      cell: (row) => formatMoney(row.revenue),
      className: "text-right",
      sortKey: "revenue",
    },
    {
      id: "customers",
      header: "Customers",
      cell: (row) => row.customers.toLocaleString("en-IN"),
      className: "text-right",
    },
    {
      id: "delivered",
      header: "Delivered",
      cell: (row) => row.delivered.toLocaleString("en-IN"),
      className: "text-right",
    },
    {
      id: "rto",
      header: "RTO",
      cell: (row) => row.rto.toLocaleString("en-IN"),
      className: "text-right",
    },
    {
      id: "rto_rate",
      header: "RTO Rate",
      cell: (row) => (row.rto_rate_pct === null ? "—" : `${row.rto_rate_pct.toFixed(1)}%`),
      className: "text-right",
      sortKey: "rto_rate_pct",
    },
    {
      id: "growth",
      header: "Growth",
      cell: (row) => <GrowthCell value={row.growth_pct} />,
      className: "text-right",
      sortKey: "growth_pct",
    },
    {
      id: "opportunity",
      header: "Classification",
      cell: (row) => <OpportunityBadge value={row.opportunity} />,
    },
  ]

  return (
    <Card>
      <CardHeader>
        <CardTitle>Top States</CardTitle>
      </CardHeader>
      <CardContent>
        {sorted.length === 0 ? (
          <p className="text-muted-foreground text-sm">
            No geographic order data available for this period.
          </p>
        ) : (
          <DataTable
            columns={columns}
            data={sorted}
            rowKey={(row) => row.state}
            onRowClick={(row) => onSelectState(row.state)}
            sortBy={sortKey}
            sortOrder={sortOrder}
            onSortChange={handleSortChange}
          />
        )}
        {selectedState && !sorted.some((s) => s.state === selectedState) ? (
          <p className="text-muted-foreground mt-2 text-xs">
            {selectedState} has no orders in this period.
          </p>
        ) : null}
      </CardContent>
    </Card>
  )
}

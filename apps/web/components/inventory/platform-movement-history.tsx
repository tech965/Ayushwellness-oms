"use client"

import * as React from "react"

import { DataTable, type DataTableColumn } from "@/components/shared/data-table"
import { PaginationBar } from "@/components/shared/pagination-bar"
import { QueryStates } from "@/components/shared/query-states"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { formatDateTime } from "@/lib/format"
import { usePaginationState } from "@/lib/use-pagination"
import { usePlatformMovementHistory } from "@/services/platform-inventory"
import { MANUAL_PLATFORMS, PLATFORM_LABELS } from "@/types/platform-inventory"
import type { UnifiedStockMovement } from "@/types/platform-inventory"

interface PlatformMovementHistoryProps {
  variantId: string
  variantLabel: string
}

/** "Platform Stock Movement History" — this variant's manual platform
 * movements interleaved with its existing Shopify movements, sorted by
 * time. An ADDITIONAL section: the page's existing, separate Shopify
 * movement-history table (`OmsVariantHistory`) is untouched.
 */
export function PlatformMovementHistory({ variantId, variantLabel }: PlatformMovementHistoryProps) {
  const { page, pageSize, setPage } = usePaginationState(20)
  const [platform, setPlatform] = React.useState<string>("")

  const query = usePlatformMovementHistory(variantId, { page, pageSize, platform })

  const columns: DataTableColumn<UnifiedStockMovement>[] = [
    { id: "when", header: "Date/Time", cell: (row) => formatDateTime(row.created_at) },
    { id: "platform", header: "Platform", cell: (row) => row.platform_label },
    {
      id: "movement",
      header: "Movement",
      cell: (row) => (
        <span className={row.quantity_delta >= 0 ? "text-emerald-600" : "text-red-600"}>
          {formatMovementLabel(row.movement_type)}:{" "}
          {row.quantity_delta > 0 ? `+${row.quantity_delta}` : row.quantity_delta}{" "}
          {Math.abs(row.quantity_delta) === 1 ? "box" : "boxes"}
        </span>
      ),
    },
    {
      id: "balance",
      header: "Stock Balance",
      className: "text-right",
      cell: (row) => `${row.quantity_after} boxes`,
    },
    { id: "reason", header: "Reason", cell: (row) => row.reason ?? "—" },
    { id: "actor", header: "Actor / Source", cell: (row) => row.actor_label },
  ]

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between gap-2">
        <CardTitle>Platform Stock Movement History — {variantLabel}</CardTitle>
        <Select
          value={platform || "__all__"}
          onValueChange={(v) => {
            setPlatform(v === "__all__" ? "" : v)
            setPage(1)
          }}
        >
          <SelectTrigger className="w-[180px]">
            <SelectValue placeholder="All platforms" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="__all__">All platforms</SelectItem>
            <SelectItem value="shopify">Shopify</SelectItem>
            {MANUAL_PLATFORMS.map((p) => (
              <SelectItem key={p} value={p}>
                {PLATFORM_LABELS[p]}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </CardHeader>
      <CardContent>
        <QueryStates
          isLoading={query.isLoading}
          isError={query.isError}
          error={query.error}
          data={query.data}
          onRetry={() => void query.refetch()}
          isEmpty={(data) => data.data.length === 0}
          emptyTitle="No stock movements yet"
        >
          {(data) => (
            <>
              <DataTable columns={columns} data={data.data} rowKey={(row) => row.id} />
              <PaginationBar meta={data.meta} onPageChange={setPage} />
            </>
          )}
        </QueryStates>
      </CardContent>
    </Card>
  )
}

function formatMovementLabel(movementType: string): string {
  switch (movementType) {
    case "stock_added":
      return "Stock Added"
    case "stock_deducted":
      return "Sold"
    case "dispatch":
      return "Shipped"
    case "rto_restock":
      return "RTO Restocked"
    case "manual_adjustment":
      return "Manual Adjustment"
    case "initial_stock":
      return "Initial Stock"
    default:
      return movementType
  }
}

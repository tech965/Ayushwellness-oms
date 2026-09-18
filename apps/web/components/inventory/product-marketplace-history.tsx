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
import { useProductMarketplaceHistory } from "@/services/platform-inventory"
import { MANUAL_PLATFORMS, PLATFORM_LABELS } from "@/types/platform-inventory"
import type { ProductMarketplaceMovement } from "@/types/platform-inventory"

interface ProductMarketplaceHistoryProps {
  productId: string
}

const MOVEMENT_TYPE_LABELS: Record<string, string> = {
  stock_added: "Stock Added",
  sale: "Sale",
  rto: "RTO",
}

/** Product-level Marketplace Adjustment history: Add Stock / Sale / RTO,
 * each its own row -- a Sale and a later RTO are never merged or netted,
 * so both always appear as separate events here. Distinct from the
 * per-SKU `PlatformMovementHistory` (unaffected by this feature).
 */
export function ProductMarketplaceHistory({ productId }: ProductMarketplaceHistoryProps) {
  const { page, pageSize, setPage } = usePaginationState(20)
  const [platform, setPlatform] = React.useState<string>("")

  const query = useProductMarketplaceHistory(productId, { page, pageSize, platform })

  const columns: DataTableColumn<ProductMarketplaceMovement>[] = [
    { id: "when", header: "Date/Time", cell: (row) => formatDateTime(row.created_at) },
    { id: "platform", header: "Platform", cell: (row) => row.platform_label },
    {
      id: "movement",
      header: "Movement Type",
      cell: (row) => MOVEMENT_TYPE_LABELS[row.movement_type] ?? row.movement_type,
    },
    {
      id: "quantity",
      header: "Quantity",
      className: "text-right",
      cell: (row) => (
        <span className={row.quantity_delta >= 0 ? "text-emerald-600" : "text-red-600"}>
          {row.quantity_delta >= 0 ? "+" : "-"}
          {row.quantity_packets} {row.quantity_packets === 1 ? "packet" : "packets"}
        </span>
      ),
    },
    { id: "reason", header: "Reason", cell: (row) => row.reason ?? "—" },
    { id: "actor", header: "Actor", cell: (row) => row.actor_label },
  ]

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between gap-2">
        <CardTitle>Marketplace Adjustment History</CardTitle>
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
          emptyTitle="No marketplace adjustments recorded yet"
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

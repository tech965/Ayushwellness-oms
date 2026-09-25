"use client"

import * as React from "react"

import { DataTable, type DataTableColumn } from "@/components/shared/data-table"
import {
  EditMarketplaceMovementDialog,
  MOVEMENT_TYPE_LABELS,
  UndoMarketplaceMovementDialog,
} from "@/components/inventory/marketplace-movement-dialogs"
import { PaginationBar } from "@/components/shared/pagination-bar"
import { QueryStates } from "@/components/shared/query-states"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { useAuth } from "@/lib/auth-context"
import { formatDateTime } from "@/lib/format"
import { usePaginationState } from "@/lib/use-pagination"
import { useProductMarketplaceHistory } from "@/services/platform-inventory"
import { MANUAL_PLATFORMS, PLATFORM_LABELS } from "@/types/platform-inventory"
import type { ProductMarketplaceMovement } from "@/types/platform-inventory"

interface ProductMarketplaceHistoryProps {
  productId: string
  /** Set for a variant-scoped product (Herbal): show ONLY this OMS-visible
   * variant's own history -- Gold's never includes Red's.
   */
  catalogVariantId?: string
  /** Card heading, e.g. "Gold Packet Marketplace History". */
  title?: string
}

function statusText(row: ProductMarketplaceMovement): string {
  switch (row.status) {
    case "reversal":
      return "Reversal"
    case "undone":
      return "Undone"
    case "edited":
      return "Edited (superseded)"
    default:
      return row.edited_from_packets !== null
        ? `Edited from ${row.edited_from_packets} → ${row.quantity_packets} packets`
        : "Active"
  }
}

/** Marketplace history: Sale / RTO / Reversal, each its own row (a Sale, its
 * reversal and any replacement are never merged or netted), with each
 * row's effective status. Edit / Undo appear only on a still-effective
 * manual Sale/RTO (`can_edit` / `can_undo`, decided by the backend) and only
 * for a user who can manage inventory -- Shopify's automatic stock never
 * appears here, so it can never be edited through this mechanism.
 */
export function ProductMarketplaceHistory({
  productId,
  catalogVariantId,
  title = "Marketplace Adjustment History",
}: ProductMarketplaceHistoryProps) {
  const { hasPermission } = useAuth()
  const canManage = hasPermission("inventory.manage")
  const { page, pageSize, setPage } = usePaginationState(20)
  const [platform, setPlatform] = React.useState<string>("")
  const [editing, setEditing] = React.useState<ProductMarketplaceMovement | null>(null)
  const [undoing, setUndoing] = React.useState<ProductMarketplaceMovement | null>(null)

  const query = useProductMarketplaceHistory(productId, {
    page,
    pageSize,
    platform,
    catalog_variant_id: catalogVariantId,
  })

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
    { id: "status", header: "Status", cell: (row) => statusText(row) },
    {
      id: "actions",
      header: "",
      cell: (row) =>
        canManage && (row.can_edit || row.can_undo) ? (
          <div className="flex justify-end gap-1.5">
            {row.can_edit && (
              <Button variant="outline" size="sm" onClick={() => setEditing(row)}>
                Edit
              </Button>
            )}
            {row.can_undo && (
              <Button variant="ghost" size="sm" onClick={() => setUndoing(row)}>
                Undo
              </Button>
            )}
          </div>
        ) : null,
    },
  ]

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between gap-2">
        <CardTitle>{title}</CardTitle>
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

      {editing && (
        <EditMarketplaceMovementDialog
          key={editing.id}
          movement={editing}
          onClose={() => setEditing(null)}
        />
      )}
      {undoing && (
        <UndoMarketplaceMovementDialog
          key={undoing.id}
          movement={undoing}
          onClose={() => setUndoing(null)}
        />
      )}
    </Card>
  )
}

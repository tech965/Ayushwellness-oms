"use client"

import * as React from "react"
import { toast } from "sonner"

import { LiveResultNotice } from "@/components/shared/live-result-notice"
import { PageHeader } from "@/components/shared/page-header"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Skeleton } from "@/components/ui/skeleton"
import { getApiErrorMessage } from "@/lib/api-client"
import { useAuth } from "@/lib/auth-context"
import { formatDateTime } from "@/lib/format"
import { useAddVariantStock, useInventoryMovements } from "@/services/inventory"
import { useInventoryOverview, type InventoryOverviewRow } from "@/services/live"

function Movements({ variantId }: { variantId: string }) {
  const query = useInventoryMovements({ page: 1, pageSize: 10, product_variant_id: variantId })
  if (query.isLoading) return <Skeleton className="h-10 w-full" />
  if (query.isError) {
    return <p className="text-sm text-destructive">{getApiErrorMessage(query.error)}</p>
  }
  const rows = query.data?.data ?? []
  if (rows.length === 0) return <p className="text-sm text-muted-foreground">No movements yet.</p>
  return (
    <table className="w-full text-xs" data-testid="live-movements">
      <thead>
        <tr className="text-left text-muted-foreground">
          <th>When</th>
          <th>Type</th>
          <th className="text-right">Change</th>
          <th className="text-right">Balance</th>
          <th>Reason</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr key={row.id} className="border-t">
            <td>{formatDateTime(row.created_at)}</td>
            <td>{row.movement_type.replace(/_/g, " ")}</td>
            <td className="text-right tabular-nums">{row.quantity_delta}</td>
            <td className="text-right tabular-nums">{row.quantity_after}</td>
            <td>{row.reason ?? "—"}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function AddStock({ row, onDone }: { row: InventoryOverviewRow; onDone: () => void }) {
  const [quantity, setQuantity] = React.useState("")
  const [reason, setReason] = React.useState("")
  const mutation = useAddVariantStock()
  return (
    <form
      className="flex flex-wrap items-end gap-2 py-2"
      onSubmit={(event) => {
        event.preventDefault()
        mutation.mutate(
          { variantId: row.variant_id, quantity_to_add: Number(quantity), reason },
          {
            onSuccess: () => {
              toast.success("Stock added.")
              onDone()
            },
            onError: (error) => toast.error(getApiErrorMessage(error)),
          }
        )
      }}
    >
      <Input
        required
        min={1}
        type="number"
        className="w-28"
        placeholder="Boxes"
        aria-label="Boxes to add"
        value={quantity}
        onChange={(e) => setQuantity(e.target.value)}
      />
      <Input
        required
        className="w-64"
        placeholder="Reason"
        aria-label="Reason"
        value={reason}
        onChange={(e) => setReason(e.target.value)}
      />
      <Button type="submit" size="sm" disabled={mutation.isPending}>
        Add stock
      </Button>
    </form>
  )
}

/** OMS inventory while the catalogue is read live: stock, reservations and
 * the movement ledger are OMS-owned; names come live from Shopify when it
 * is reachable and the page says so when it is not. */
export function LiveInventory() {
  const { hasPermission } = useAuth()
  const [page, setPage] = React.useState(1)
  const [q, setQ] = React.useState("")
  const [open, setOpen] = React.useState<{ id: string; mode: "ledger" | "add" } | null>(null)
  const query = useInventoryOverview(page, q)
  const canManage = hasPermission("inventory.manage")

  return (
    <>
      <PageHeader
        title="Inventory"
        description="Stock is OMS-owned (default warehouse). Product names are read live from Shopify."
      />
      <div className="pb-3">
        <Input
          className="w-64"
          placeholder="Search SKU"
          aria-label="Search SKU"
          value={q}
          onChange={(e) => {
            setQ(e.target.value)
            setPage(1)
          }}
        />
      </div>
      {query.isError ? (
        <Alert variant="destructive">
          <AlertTitle>Could not load inventory</AlertTitle>
          <AlertDescription>{getApiErrorMessage(query.error)}</AlertDescription>
        </Alert>
      ) : null}
      {query.isLoading ? <Skeleton className="h-40 w-full" /> : null}
      {query.data ? (
        <>
          {query.data.warnings.length > 0 ? (
            <LiveResultNotice meta={{ warnings: query.data.warnings }} />
          ) : null}
          <table className="w-full text-sm" data-testid="live-inventory">
            <thead>
              <tr className="text-left text-muted-foreground">
                <th className="py-2">SKU</th>
                <th>Name</th>
                <th className="text-right">Physical</th>
                <th className="text-right">Reserved</th>
                <th className="text-right">Available</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {query.data.items.map((row) => (
                <React.Fragment key={row.variant_id}>
                  <tr className="border-t">
                    <td className="py-2">{row.oms_sku}</td>
                    <td>
                      {row.oms_display_name ??
                        row.shopify_title ??
                        (row.mapped ? "Name unavailable" : "Not mapped to Shopify")}
                    </td>
                    <td className="text-right tabular-nums">{row.physical_quantity}</td>
                    <td className="text-right tabular-nums">{row.reserved_quantity}</td>
                    <td className="text-right tabular-nums">{row.available_to_promise}</td>
                    <td className="text-right">
                      <Button
                        variant="link"
                        size="sm"
                        onClick={() =>
                          setOpen(open?.id === row.variant_id && open.mode === "ledger" ? null : { id: row.variant_id, mode: "ledger" })
                        }
                      >
                        Ledger
                      </Button>
                      {canManage ? (
                        <Button
                          variant="link"
                          size="sm"
                          onClick={() =>
                            setOpen(open?.id === row.variant_id && open.mode === "add" ? null : { id: row.variant_id, mode: "add" })
                          }
                        >
                          Add stock
                        </Button>
                      ) : null}
                    </td>
                  </tr>
                  {open?.id === row.variant_id ? (
                    <tr>
                      <td colSpan={6}>
                        {open.mode === "ledger" ? (
                          <Movements variantId={row.variant_id} />
                        ) : (
                          <AddStock row={row} onDone={() => setOpen(null)} />
                        )}
                      </td>
                    </tr>
                  ) : null}
                </React.Fragment>
              ))}
            </tbody>
          </table>
          <div className="flex items-center gap-2 pt-3">
            <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => setPage(page - 1)}>
              Previous
            </Button>
            <span className="text-sm text-muted-foreground">
              Page {page} of {Math.max(1, Math.ceil(query.data.total_items / query.data.page_size))}
            </span>
            <Button
              variant="outline"
              size="sm"
              disabled={page * query.data.page_size >= query.data.total_items}
              onClick={() => setPage(page + 1)}
            >
              Next
            </Button>
          </div>
        </>
      ) : null}
    </>
  )
}

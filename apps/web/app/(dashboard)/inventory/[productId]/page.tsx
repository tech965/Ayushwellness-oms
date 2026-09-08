"use client"

import * as React from "react"
import { useParams } from "next/navigation"
import { toast } from "sonner"

import { DataTable, type DataTableColumn } from "@/components/shared/data-table"
import { FilterBar } from "@/components/shared/filter-bar"
import { PageHeader } from "@/components/shared/page-header"
import { PaginationBar } from "@/components/shared/pagination-bar"
import { QueryStates } from "@/components/shared/query-states"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { getApiErrorMessage } from "@/lib/api-client"
import { useAuth } from "@/lib/auth-context"
import { formatDateTime } from "@/lib/format"
import { cn } from "@/lib/utils"
import { usePaginationState } from "@/lib/use-pagination"
import {
  useAdjustStock,
  useInventoryMovements,
  useInventoryProductVariants,
  useUpdatePacketsPerBox,
} from "@/services/inventory"
import {
  INVENTORY_MOVEMENT_TYPE_OPTIONS,
  STOCK_STATUS_BADGE_CLASSES,
  STOCK_STATUS_LABELS,
  type InventoryMovement,
  type InventoryMovementType,
  type InventoryVariant,
} from "@/types/inventory"

function StockStatusBadge({ status }: { status: InventoryVariant["stock_status"] }) {
  return (
    <Badge
      variant="outline"
      className={cn("rounded-md border-transparent font-semibold", STOCK_STATUS_BADGE_CLASSES[status])}
    >
      {STOCK_STATUS_LABELS[status]}
    </Badge>
  )
}

/** Absolute-target stock adjustment -- staff enters the NEW total, never a
 * raw +/- delta. Deliberately a separate dialog from packets-per-box (spec:
 * "Do not mix stock adjustment and packets-per-box change into one
 * ambiguous operation"). The backend is authoritative for the delta/new
 * balance math -- this preview is display-only and is recomputed from
 * whatever the server returns on save, never trusted as the actual result.
 */
function AdjustStockDialog({ variant }: { variant: InventoryVariant }) {
  const [open, setOpen] = React.useState(false)
  const [target, setTarget] = React.useState(String(variant.available_boxes))
  const [reason, setReason] = React.useState("")
  const adjust = useAdjustStock(variant.id)

  function openDialog() {
    setTarget(String(variant.available_boxes))
    setReason("")
    setOpen(true)
  }

  const parsedTarget = Number(target)
  const hasValidTarget = target !== "" && Number.isInteger(parsedTarget) && parsedTarget >= 0
  const canSubmit = hasValidTarget && parsedTarget !== variant.available_boxes && reason.trim().length > 0
  const delta = hasValidTarget ? parsedTarget - variant.available_boxes : 0

  function submit() {
    adjust.mutate(
      { target_boxes: parsedTarget, reason: reason.trim() },
      {
        onSuccess: () => {
          toast.success("Stock adjusted.")
          setOpen(false)
          setReason("")
        },
        onError: (error) => toast.error(getApiErrorMessage(error)),
      }
    )
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <Button variant="outline" size="sm" onClick={openDialog}>
        Edit Stock
      </Button>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Edit inventory — {variant.variant_title ?? variant.sku}</DialogTitle>
        </DialogHeader>
        <div className="flex flex-col gap-3">
          <div className="grid grid-cols-2 gap-3 text-sm">
            <div>
              <div className="text-muted-foreground">Current stock</div>
              <div className="text-foreground font-semibold">{variant.available_boxes} boxes</div>
            </div>
            <div>
              <div className="text-muted-foreground">Packets per box</div>
              <div className="text-foreground font-semibold">{variant.packets_per_box}</div>
            </div>
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="target">New stock (boxes)</Label>
            <Input
              id="target"
              type="number"
              min={0}
              value={target}
              onChange={(e) => setTarget(e.target.value)}
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="reason">Reason</Label>
            <Input
              id="reason"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="e.g. New warehouse stock received"
            />
          </div>
          {hasValidTarget && (
            <div className="bg-muted/50 rounded-md p-3 text-sm">
              <div>
                Adjustment:{" "}
                <span className={cn("font-semibold", delta < 0 ? "text-red-600" : "text-emerald-600")}>
                  {delta > 0 ? "+" : ""}
                  {delta} boxes
                </span>
              </div>
              <div>
                New balance: <span className="font-semibold">{parsedTarget} boxes</span>
              </div>
            </div>
          )}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => setOpen(false)}>
            Cancel
          </Button>
          <Button disabled={!canSubmit || adjust.isPending} onClick={submit}>
            {adjust.isPending ? "Saving..." : "Save Adjustment"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

/** Packets-per-box configuration -- changes only the packets<->boxes
 * conversion/display, never `available_boxes` itself, and never rewrites
 * historical movement quantities (each ledger row keeps the box amount
 * that was actually moved at the time -- see `InventoryService.
 * apply_rto_restock`'s dispatch-consistency fix).
 */
function PacketsPerBoxDialog({ variant }: { variant: InventoryVariant }) {
  const [open, setOpen] = React.useState(false)
  const [value, setValue] = React.useState(String(variant.packets_per_box))
  const update = useUpdatePacketsPerBox(variant.id)

  function openDialog() {
    setValue(String(variant.packets_per_box))
    setOpen(true)
  }

  const parsed = Number(value)
  const canSubmit = value !== "" && Number.isInteger(parsed) && parsed > 0

  function submit() {
    update.mutate(
      { packets_per_box: parsed },
      {
        onSuccess: () => {
          toast.success("Packets per box updated.")
          setOpen(false)
        },
        onError: (error) => toast.error(getApiErrorMessage(error)),
      }
    )
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <Button variant="ghost" size="sm" onClick={openDialog}>
        Packets/box
      </Button>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Packets per box — {variant.variant_title ?? variant.sku}</DialogTitle>
        </DialogHeader>
        <div className="flex flex-col gap-3">
          <p className="text-muted-foreground text-sm">
            Changes only how packets are converted/displayed — the box count itself
            ({variant.available_boxes} boxes) and past movement history are not affected.
          </p>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="ppb">Packets per box</Label>
            <Input
              id="ppb"
              type="number"
              min={1}
              value={value}
              onChange={(e) => setValue(e.target.value)}
            />
          </div>
        </div>
        <DialogFooter>
          <Button disabled={!canSubmit || update.isPending} onClick={submit}>
            {update.isPending ? "Saving..." : "Save"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function VariantCard({
  variant,
  onViewHistory,
}: {
  variant: InventoryVariant
  onViewHistory: (variant: InventoryVariant) => void
}) {
  const { hasPermission } = useAuth()
  const canManage = hasPermission("inventory.manage")

  return (
    <Card>
      <CardHeader className="flex-row items-start justify-between">
        <div>
          <CardTitle>{variant.variant_title ?? variant.sku}</CardTitle>
          <p className="text-muted-foreground text-sm">SKU: {variant.sku}</p>
        </div>
        <StockStatusBadge status={variant.stock_status} />
      </CardHeader>
      <CardContent>
        <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm sm:grid-cols-4">
          <div>
            <dt className="text-muted-foreground">Packets/box</dt>
            <dd className="font-medium">{variant.packets_per_box}</dd>
          </div>
          <div>
            <dt className="text-muted-foreground">Available boxes</dt>
            <dd className="font-medium">{variant.available_boxes}</dd>
          </div>
          <div>
            <dt className="text-muted-foreground">Total packets</dt>
            <dd className="font-medium">{variant.total_packets.toLocaleString()}</dd>
          </div>
          <div>
            <dt className="text-muted-foreground">Shopify qty (reference)</dt>
            <dd className="text-muted-foreground">{variant.shopify_inventory_quantity}</dd>
          </div>
        </dl>
        <div className="mt-4 flex flex-wrap gap-2">
          {canManage && (
            <>
              <AdjustStockDialog variant={variant} />
              <PacketsPerBoxDialog variant={variant} />
            </>
          )}
          <Button variant="ghost" size="sm" onClick={() => onViewHistory(variant)}>
            History
          </Button>
        </div>
      </CardContent>
    </Card>
  )
}

function movementTypeLabel(type: InventoryMovement["movement_type"]): string {
  return INVENTORY_MOVEMENT_TYPE_OPTIONS.find((option) => option.value === type)?.label ?? type
}

function MovementHistorySection({
  productId,
  selectedVariant,
  onClearVariant,
}: {
  productId: string
  selectedVariant: InventoryVariant | null
  onClearVariant: () => void
}) {
  const { page, pageSize, setPage, resetPage } = usePaginationState()
  const [movementType, setMovementType] = React.useState<InventoryMovementType | undefined>(
    undefined
  )
  const query = useInventoryMovements({
    page,
    pageSize,
    product_id: productId,
    product_variant_id: selectedVariant?.id,
    movement_type: movementType,
  })

  const columns: DataTableColumn<InventoryMovement>[] = [
    { id: "when", header: "Date/time", cell: (row) => formatDateTime(row.created_at) },
    { id: "variant", header: "Variant", cell: (row) => row.variant_title ?? row.sku ?? "—" },
    { id: "sku", header: "SKU", cell: (row) => row.sku ?? "—" },
    {
      id: "type",
      header: "Movement",
      cell: (row) => <Badge variant="secondary">{movementTypeLabel(row.movement_type)}</Badge>,
    },
    {
      id: "delta",
      header: "Change (boxes)",
      cell: (row) => (
        <span className={row.quantity_delta < 0 ? "text-red-600" : "text-emerald-600"}>
          {row.quantity_delta > 0 ? `+${row.quantity_delta}` : row.quantity_delta}
        </span>
      ),
    },
    { id: "previous", header: "Previous balance", cell: (row) => `${row.previous_balance} boxes` },
    { id: "new", header: "New balance", cell: (row) => `${row.quantity_after} boxes` },
    {
      id: "reference",
      header: "Reference",
      cell: (row) => (row.shipment_id ? "Shipment" : row.rto_id ? "RTO" : row.order_id ? "Order" : "—"),
    },
    { id: "reason", header: "Reason", cell: (row) => row.reason ?? "—" },
    { id: "actor", header: "Actor/source", cell: (row) => row.actor_label },
  ]

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-lg font-semibold">
          {selectedVariant
            ? `Movement History — ${selectedVariant.variant_title ?? selectedVariant.sku}`
            : "Movement History — all variants"}
        </h2>
        {selectedVariant && (
          <Button variant="link" size="sm" className="h-auto p-0" onClick={onClearVariant}>
            Show all variants
          </Button>
        )}
      </div>
      <FilterBar
        statusValue={movementType}
        onStatusChange={(value) => {
          setMovementType(value as InventoryMovementType | undefined)
          resetPage()
        }}
        statusOptions={INVENTORY_MOVEMENT_TYPE_OPTIONS}
        statusLabel="Movement type"
      />
      <QueryStates
        isLoading={query.isLoading}
        isError={query.isError}
        error={query.error}
        data={query.data}
        onRetry={() => void query.refetch()}
        isEmpty={(data) => data.data.length === 0}
        emptyTitle="No stock movements yet"
        emptyDescription="Dispatches, RTO restocks, and manual adjustments will appear here."
      >
        {(data) => (
          <>
            <DataTable columns={columns} data={data.data} rowKey={(row) => row.id} />
            <PaginationBar meta={data.meta} onPageChange={setPage} />
          </>
        )}
      </QueryStates>
    </div>
  )
}

export default function InventoryProductPage() {
  const params = useParams<{ productId: string }>()
  const productId = params.productId
  const query = useInventoryProductVariants(productId)
  const [selectedVariant, setSelectedVariant] = React.useState<InventoryVariant | null>(null)

  return (
    <>
      <PageHeader
        title={query.data?.product_title ?? "Product variants"}
        description="Product → Variant → Inventory. Stock is in boxes; each variant configures its own packets per box."
        backHref="/inventory"
        backLabel="Back to Inventory"
      />
      <div className="flex flex-col gap-8">
        <QueryStates
          isLoading={query.isLoading}
          isError={query.isError}
          error={query.error}
          data={query.data}
          isEmpty={(data) => data.variants.length === 0}
          onRetry={() => void query.refetch()}
          emptyTitle="No variants found"
          emptyDescription="This product has no variants yet."
        >
          {(data) => (
            <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
              {data.variants.map((variant) => (
                <VariantCard key={variant.id} variant={variant} onViewHistory={setSelectedVariant} />
              ))}
            </div>
          )}
        </QueryStates>

        <MovementHistorySection
          productId={productId}
          selectedVariant={selectedVariant}
          onClearVariant={() => setSelectedVariant(null)}
        />
      </div>
    </>
  )
}

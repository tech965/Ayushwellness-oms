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
  useAdjustProductStock,
  useAdjustVariantStock,
  useInventoryMovements,
  useInventoryProductStock,
  useSetProductName,
  useSetVariantName,
  useUpdatePacketsPerBox,
} from "@/services/inventory"
import {
  INVENTORY_MOVEMENT_TYPE_OPTIONS,
  STOCK_STATUS_BADGE_CLASSES,
  STOCK_STATUS_LABELS,
  type InventoryMovement,
  type InventoryMovementType,
  type InventoryProductStock,
  type ProductVariantStockLine,
  type StockStatus,
} from "@/types/inventory"

function StockStatusBadge({ status }: { status: StockStatus }) {
  return (
    <Badge
      variant="outline"
      className={cn(
        "rounded-md border-transparent font-semibold",
        STOCK_STATUS_BADGE_CLASSES[status]
      )}
    >
      {STOCK_STATUS_LABELS[status]}
    </Badge>
  )
}

/** Manual "Edit Name" -- sets a custom display name the Inventory UI shows
 * instead of the Shopify name. Presentational only: the backend never
 * touches the real Shopify title, stock, or the movement ledger, and a
 * later Shopify product sync does not overwrite the custom name.
 * "Reset to Shopify Name" (shown only when a custom name is set) clears it.
 * `mutation` is `useSetProductName(id)` / `useSetVariantName(id)` -- same
 * shape; pass `null` to reset, a trimmed string to set.
 */
export function EditNameDialog({
  kind,
  currentDisplayName,
  shopifyName,
  hasOverride,
  maxLength,
  mutation,
}: {
  kind: "product" | "variant"
  currentDisplayName: string
  shopifyName: string | null
  hasOverride: boolean
  maxLength: number
  mutation: ReturnType<typeof useSetProductName>
}) {
  const [open, setOpen] = React.useState(false)
  const [value, setValue] = React.useState(currentDisplayName)

  function openDialog() {
    setValue(currentDisplayName)
    setOpen(true)
  }

  const trimmed = value.trim()
  const isEmpty = trimmed.length === 0
  const canSave =
    !isEmpty && trimmed.length <= maxLength && trimmed !== currentDisplayName

  function save() {
    mutation.mutate(trimmed, {
      onSuccess: () => {
        toast.success(
          kind === "product" ? "Product name updated." : "Variant name updated."
        )
        setOpen(false)
      },
      onError: (error) => toast.error(getApiErrorMessage(error)),
    })
  }

  function reset() {
    mutation.mutate(null, {
      onSuccess: () => {
        toast.success("Reset to Shopify name.")
        setOpen(false)
      },
      onError: (error) => toast.error(getApiErrorMessage(error)),
    })
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <Button variant="ghost" size="sm" onClick={openDialog}>
        Edit Name
      </Button>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>
            {kind === "product" ? "Edit Product Name" : "Edit Variant Name"}
          </DialogTitle>
        </DialogHeader>
        <div className="flex flex-col gap-3">
          {shopifyName && (
            <p className="text-muted-foreground text-sm">
              Shopify name: <span className="text-foreground">{shopifyName}</span>
            </p>
          )}
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="display-name">Display name</Label>
            <Input
              id="display-name"
              value={value}
              maxLength={maxLength}
              onChange={(e) => setValue(e.target.value)}
              autoFocus
            />
            {isEmpty && <p className="text-sm text-red-600">Name cannot be empty.</p>}
          </div>
        </div>
        <DialogFooter className="sm:justify-between">
          {hasOverride ? (
            <Button variant="ghost" onClick={reset} disabled={mutation.isPending}>
              Reset to Shopify Name
            </Button>
          ) : (
            <span />
          )}
          <div className="flex gap-2">
            <Button variant="outline" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button disabled={!canSave || mutation.isPending} onClick={save}>
              {mutation.isPending ? "Saving..." : "Save"}
            </Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function ProductNameEditor({
  productId,
  displayName,
  shopifyName,
  hasOverride,
}: {
  productId: string
  displayName: string
  shopifyName: string
  hasOverride: boolean
}) {
  const mutation = useSetProductName(productId)
  return (
    <EditNameDialog
      kind="product"
      currentDisplayName={displayName}
      shopifyName={shopifyName}
      hasOverride={hasOverride}
      maxLength={500}
      mutation={mutation}
    />
  )
}

function VariantNameEditor({ variant }: { variant: ProductVariantStockLine }) {
  const mutation = useSetVariantName(variant.id)
  return (
    <EditNameDialog
      kind="variant"
      currentDisplayName={variant.display_title}
      shopifyName={variant.variant_title}
      hasOverride={variant.variant_title_override !== null}
      maxLength={255}
      mutation={mutation}
    />
  )
}

/** Product-level "Edit Stock". Absolute target, never a raw +/- delta.
 * One product = ONE card, but the underlying variant records are edited
 * directly: a single-variant product gets one "New stock" field (backend
 * forwards to that variant); a multi-variant product gets one row per
 * underlying variant -- no product-level distribution rule is invented.
 * The backend computes the delta / new balance and writes one
 * `InventoryMovement` per changed variant.
 */
function EditStockDialog({ product }: { product: InventoryProductStock }) {
  const single = product.variant_count === 1
  const adjustProduct = useAdjustProductStock(product.product_id)
  const adjustVariant = useAdjustVariantStock()

  const [open, setOpen] = React.useState(false)
  const [reason, setReason] = React.useState("")
  const [targets, setTargets] = React.useState<Record<string, string>>({})

  function openDialog() {
    setReason("")
    setTargets(
      Object.fromEntries(product.variants.map((v) => [v.id, String(v.available_boxes)]))
    )
    setOpen(true)
  }

  const rows = product.variants.map((v) => {
    const raw = targets[v.id] ?? String(v.available_boxes)
    const parsed = Number(raw)
    const valid = raw !== "" && Number.isInteger(parsed) && parsed >= 0
    const delta = valid ? parsed - v.available_boxes : 0
    return {
      v,
      raw,
      parsed,
      valid,
      delta,
      changed: valid && parsed !== v.available_boxes,
    }
  })
  const anyInvalid = rows.some((r) => !r.valid)
  const changedRows = rows.filter((r) => r.changed)
  const canSave = !anyInvalid && changedRows.length > 0 && reason.trim().length > 0
  const pending = adjustProduct.isPending || adjustVariant.isPending

  async function save() {
    try {
      if (single) {
        await adjustProduct.mutateAsync({
          target_boxes: changedRows[0].parsed,
          reason: reason.trim(),
        })
      } else {
        for (const r of changedRows) {
          await adjustVariant.mutateAsync({
            variantId: r.v.id,
            target_boxes: r.parsed,
            reason: reason.trim(),
          })
        }
      }
      toast.success(
        changedRows.length === 1
          ? "Stock adjusted."
          : `Stock adjusted for ${changedRows.length} variants.`
      )
      setOpen(false)
    } catch (error) {
      toast.error(getApiErrorMessage(error))
    }
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <Button variant="outline" size="sm" onClick={openDialog}>
        Edit Stock
      </Button>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>Edit Stock — {product.product_name}</DialogTitle>
        </DialogHeader>
        <div className="flex flex-col gap-3">
          {!single && (
            <p className="text-muted-foreground text-sm">
              Enter the new box count for each variant you want to change. Each edit is
              recorded as its own inventory movement.
            </p>
          )}
          <div className="flex flex-col gap-3">
            {rows.map(({ v, raw, valid, delta }) => (
              <div key={v.id} className="flex flex-col gap-1.5">
                {!single && (
                  <div className="text-sm font-medium">
                    {v.display_title}
                    <span className="text-muted-foreground font-normal">
                      {" "}
                      · SKU {v.sku}
                    </span>
                  </div>
                )}
                <div className="flex items-center gap-3 text-sm">
                  <span className="text-muted-foreground">
                    Current:{" "}
                    <span className="text-foreground font-semibold">
                      {v.available_boxes}
                    </span>{" "}
                    boxes
                  </span>
                  <div className="flex items-center gap-1.5">
                    <Label
                      htmlFor={`target-${v.id}`}
                      className="text-muted-foreground font-normal"
                    >
                      New
                    </Label>
                    <Input
                      id={`target-${v.id}`}
                      type="number"
                      min={0}
                      className="h-8 w-24"
                      value={raw}
                      onChange={(e) =>
                        setTargets((prev) => ({ ...prev, [v.id]: e.target.value }))
                      }
                    />
                  </div>
                  {valid && delta !== 0 && (
                    <span
                      className={cn(
                        "font-semibold",
                        delta < 0 ? "text-red-600" : "text-emerald-600"
                      )}
                    >
                      {delta > 0 ? "+" : ""}
                      {delta} boxes
                    </span>
                  )}
                </div>
              </div>
            ))}
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
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => setOpen(false)}>
            Cancel
          </Button>
          <Button disabled={!canSave || pending} onClick={save}>
            {pending ? "Saving..." : "Save"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

/** Packets-per-box configuration for one underlying variant -- changes
 * only the packets<->boxes conversion/display, never `available_boxes`
 * itself, and never rewrites historical movement quantities.
 */
function PacketsPerBoxDialog({ variant }: { variant: ProductVariantStockLine }) {
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
          <DialogTitle>Packets per box — {variant.display_title}</DialogTitle>
        </DialogHeader>
        <div className="flex flex-col gap-3">
          <p className="text-muted-foreground text-sm">
            Changes only how packets are converted/displayed — the box count itself (
            {variant.available_boxes} boxes) and past movement history are not affected.
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

/** The underlying variant records, kept intact but folded into the ONE
 * product card as a collapsible list (rows, not separate cards). Lets an
 * admin still rename a variant or set its packets-per-box.
 */
function UnderlyingVariants({
  product,
  canManage,
}: {
  product: InventoryProductStock
  canManage: boolean
}) {
  return (
    <details className="mt-4">
      <summary className="text-muted-foreground cursor-pointer text-sm select-none">
        Underlying variants ({product.variant_count})
      </summary>
      <div className="mt-2 flex flex-col divide-y rounded-md border">
        {product.variants.map((v) => (
          <div
            key={v.id}
            className="flex flex-wrap items-center justify-between gap-2 p-2 text-sm"
          >
            <div>
              <span className="font-medium">{v.display_title}</span>
              <span className="text-muted-foreground">
                {" "}
                · SKU {v.sku} · {v.available_boxes} boxes · {v.packets_per_box}/box
              </span>
            </div>
            {canManage && (
              <div className="flex gap-1">
                <VariantNameEditor variant={v} />
                <PacketsPerBoxDialog variant={v} />
              </div>
            )}
          </div>
        ))}
      </div>
    </details>
  )
}

function ProductStockCard({
  product,
  historyShown,
  onToggleHistory,
}: {
  product: InventoryProductStock
  historyShown: boolean
  onToggleHistory: () => void
}) {
  const { hasPermission } = useAuth()
  const canManage = hasPermission("inventory.manage")
  const packSize =
    product.packets_per_box_uniform && product.variants[0]
      ? `${product.variants[0].packets_per_box} packets/box`
      : "mixed pack sizes"

  return (
    <Card>
      <CardHeader className="flex-row items-start justify-between">
        <div>
          <CardTitle>{product.product_name}</CardTitle>
          <p className="text-muted-foreground text-sm">
            {product.variant_count} variant{product.variant_count === 1 ? "" : "s"} ·{" "}
            {packSize}
          </p>
        </div>
        <StockStatusBadge status={product.stock_status} />
      </CardHeader>
      <CardContent>
        <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm sm:grid-cols-3">
          <div>
            <dt className="text-muted-foreground">Available stock</dt>
            <dd className="font-medium">
              {product.available_boxes.toLocaleString()} boxes
            </dd>
          </div>
          <div>
            <dt className="text-muted-foreground">Total packets</dt>
            <dd className="font-medium">{product.total_packets.toLocaleString()}</dd>
          </div>
          <div>
            <dt className="text-muted-foreground">Status</dt>
            <dd className="font-medium">{STOCK_STATUS_LABELS[product.stock_status]}</dd>
          </div>
        </dl>
        <div className="mt-4 flex flex-wrap gap-2">
          {canManage && <EditStockDialog product={product} />}
          {canManage && (
            <ProductNameEditor
              productId={product.product_id}
              displayName={product.product_name}
              shopifyName={product.title}
              hasOverride={product.title_override !== null}
            />
          )}
          <Button variant="ghost" size="sm" onClick={onToggleHistory}>
            {historyShown ? "Hide History" : "History"}
          </Button>
        </div>
        <UnderlyingVariants product={product} canManage={canManage} />
      </CardContent>
    </Card>
  )
}

function movementTypeLabel(type: InventoryMovement["movement_type"]): string {
  return (
    INVENTORY_MOVEMENT_TYPE_OPTIONS.find((option) => option.value === type)?.label ?? type
  )
}

function MovementHistorySection({
  productId,
  productName,
}: {
  productId: string
  productName: string
}) {
  const { page, pageSize, setPage, resetPage } = usePaginationState()
  const [movementType, setMovementType] = React.useState<
    InventoryMovementType | undefined
  >(undefined)
  const query = useInventoryMovements({
    page,
    pageSize,
    product_id: productId,
    movement_type: movementType,
  })

  const columns: DataTableColumn<InventoryMovement>[] = [
    { id: "when", header: "Date/time", cell: (row) => formatDateTime(row.created_at) },
    {
      id: "variant",
      header: "Variant",
      cell: (row) => row.variant_display_title ?? row.variant_title ?? row.sku ?? "—",
    },
    { id: "sku", header: "SKU", cell: (row) => row.sku ?? "—" },
    {
      id: "type",
      header: "Movement",
      cell: (row) => (
        <Badge variant="secondary">{movementTypeLabel(row.movement_type)}</Badge>
      ),
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
    {
      id: "previous",
      header: "Previous balance",
      cell: (row) => `${row.previous_balance} boxes`,
    },
    { id: "new", header: "New balance", cell: (row) => `${row.quantity_after} boxes` },
    {
      id: "reference",
      header: "Reference",
      cell: (row) =>
        row.shipment_id ? "Shipment" : row.rto_id ? "RTO" : row.order_id ? "Order" : "—",
    },
    { id: "reason", header: "Reason", cell: (row) => row.reason ?? "—" },
    { id: "actor", header: "Actor/source", cell: (row) => row.actor_label },
  ]

  return (
    <div className="flex flex-col gap-3">
      <h2 className="text-lg font-semibold">Movement History — {productName}</h2>
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
  const query = useInventoryProductStock(productId)
  const [showHistory, setShowHistory] = React.useState(true)

  const product = query.data

  return (
    <>
      <PageHeader
        title={product?.product_name ?? "Product inventory"}
        description="One inventory card per product. Stock is in boxes, owned by the OMS and moved only by manual edits and Shiprocket dispatch/RTO events — never by Shopify."
        backHref="/inventory"
        backLabel="Back to Inventory"
      />
      <div className="flex flex-col gap-8">
        <QueryStates
          isLoading={query.isLoading}
          isError={query.isError}
          error={query.error}
          data={query.data}
          isEmpty={(data) => data.variant_count === 0}
          onRetry={() => void query.refetch()}
          emptyTitle="No stock records"
          emptyDescription="This product has no variants yet."
        >
          {(data) => (
            <ProductStockCard
              product={data}
              historyShown={showHistory}
              onToggleHistory={() => setShowHistory((s) => !s)}
            />
          )}
        </QueryStates>

        {showHistory && product && (
          <MovementHistorySection
            productId={productId}
            productName={product.product_name}
          />
        )}
      </div>
    </>
  )
}

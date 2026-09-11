"use client"

import * as React from "react"
import { useParams } from "next/navigation"
import { toast } from "sonner"

import { DataTable, type DataTableColumn } from "@/components/shared/data-table"
import { FilterBar } from "@/components/shared/filter-bar"
import { PageHeader } from "@/components/shared/page-header"
import { PaginationBar } from "@/components/shared/pagination-bar"
import { ProductThumbnail } from "@/components/shared/product-thumbnail"
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
  useAddCatalogVariantStock,
  useAddVariantStock,
  useCatalogVariantAdjustments,
  useInventoryMovements,
  useInventoryProductStock,
  useSetCatalogVariantName,
  useSetProductName,
  useSetVariantName,
  useUpdatePacketsPerBox,
  useUpdatePackSize,
} from "@/services/inventory"
import {
  INVENTORY_MOVEMENT_TYPE_OPTIONS,
  STOCK_STATUS_BADGE_CLASSES,
  STOCK_STATUS_LABELS,
  type CatalogVariantStockAdjustment,
  type InventoryMovement,
  type InventoryMovementType,
  type InventoryProductStock,
  type OmsCatalogVariant,
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

/** Manual "Edit Name" for a PRODUCT or an underlying Shopify VARIANT --
 * sets a custom display name (title override). Presentational only: the
 * backend never touches the real Shopify title, stock, or the movement
 * ledger, and a later Shopify sync does not overwrite the custom name.
 * "Reset to Shopify Name" (shown only when a custom name is set) clears it.
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

/** Rename an OMS-visible catalog variant (e.g. "Ghutka Flavour"). For an
 * *implicit* OMS variant (`catalog_variant_id === null`, 1:1 with its
 * single underlying Shopify row) this falls back to editing that row's
 * title override instead.
 */
function OmsVariantNameEditor({ group }: { group: OmsCatalogVariant }) {
  if (group.catalog_variant_id === null) {
    const only = group.underlying_variants[0]
    return only ? <VariantNameEditor variant={only} /> : null
  }
  return (
    <CatalogVariantNameDialog id={group.catalog_variant_id} currentName={group.name} />
  )
}

function CatalogVariantNameDialog({
  id,
  currentName,
}: {
  id: string
  currentName: string
}) {
  const mutation = useSetCatalogVariantName(id)
  const [open, setOpen] = React.useState(false)
  const [value, setValue] = React.useState(currentName)

  function openDialog() {
    setValue(currentName)
    setOpen(true)
  }

  const trimmed = value.trim()
  const isEmpty = trimmed.length === 0
  const canSave = !isEmpty && trimmed.length <= 255 && trimmed !== currentName

  function save() {
    mutation.mutate(trimmed, {
      onSuccess: () => {
        toast.success("Catalog variant renamed.")
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
          <DialogTitle>Edit Variant Name</DialogTitle>
        </DialogHeader>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="cv-name">Display name</Label>
          <Input
            id="cv-name"
            value={value}
            maxLength={255}
            onChange={(e) => setValue(e.target.value)}
            autoFocus
          />
          {isEmpty && <p className="text-sm text-red-600">Name cannot be empty.</p>}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => setOpen(false)}>
            Cancel
          </Button>
          <Button disabled={!canSave || mutation.isPending} onClick={save}>
            {mutation.isPending ? "Saving..." : "Save"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

/** "Add Stock" for a SINGLE-SKU OMS-visible variant (the group maps 1:1
 * to one underlying `ProductVariant`). Staff enters ONLY the incoming
 * quantity -- never the resulting total -- via
 * `POST /inventory/stock/{id}/adjust`.
 */
function SingleSkuAddStockDialog({ group }: { group: OmsCatalogVariant }) {
  const add = useAddVariantStock()
  const [open, setOpen] = React.useState(false)
  const [reason, setReason] = React.useState("")
  const [quantity, setQuantity] = React.useState("")
  const variant = group.underlying_variants[0]

  function openDialog() {
    setReason("")
    setQuantity("")
    setOpen(true)
  }

  const parsed = Number(quantity)
  const valid = quantity !== "" && Number.isInteger(parsed) && parsed > 0
  const canSave = Boolean(variant) && valid && reason.trim().length > 0

  async function save() {
    if (!variant) return
    try {
      const result = await add.mutateAsync({
        variantId: variant.id,
        quantity_to_add: parsed,
        reason: reason.trim(),
      })
      toast.success(
        `Stock Added: +${parsed} boxes. New Stock: ${result?.quantity_after ?? "—"} boxes.`
      )
      setOpen(false)
    } catch (error) {
      toast.error(getApiErrorMessage(error))
    }
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <Button variant="outline" size="sm" onClick={openDialog}>
        Add Stock
      </Button>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>Add Stock — {group.name}</DialogTitle>
        </DialogHeader>
        <div className="flex flex-col gap-3">
          <div className="text-sm">
            Current Stock:{" "}
            <span className="font-semibold">{variant?.available_boxes ?? 0} boxes</span>
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="quantity-to-add">Quantity to Add</Label>
            <Input
              id="quantity-to-add"
              type="number"
              min={1}
              className="w-32"
              value={quantity}
              onChange={(e) => setQuantity(e.target.value)}
              placeholder="0"
              autoFocus
            />
          </div>
          {valid && (
            <span className="font-semibold text-emerald-600">
              New Stock: {(variant?.available_boxes ?? 0) + parsed} boxes
            </span>
          )}
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
          <Button disabled={!canSave || add.isPending} onClick={save}>
            {add.isPending ? "Saving..." : "Add Stock"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

/** "Add Stock" for a MULTI-SKU OMS-visible variant (e.g. Blue Packet,
 * grouping 60/120/180). ONE incoming quantity added to the whole
 * CatalogVariant's total -- never a per-SKU input. The 60/120/180 rows
 * underneath stay read-only informational; this dialog never shows or
 * submits their individual values. Saved via
 * `POST /inventory/catalog-variants/{id}/adjust`, which records the
 * addition on the CatalogVariant's own reconciliation ledger and never
 * writes to any underlying `ProductVariant` row (see
 * `InventoryService.add_catalog_variant_stock`).
 */
function MultiSkuAddStockDialog({ group }: { group: OmsCatalogVariant }) {
  const catalogVariantId = group.catalog_variant_id
  const add = useAddCatalogVariantStock()
  const [open, setOpen] = React.useState(false)
  const [reason, setReason] = React.useState("")
  const [quantity, setQuantity] = React.useState("")

  function openDialog() {
    setReason("")
    setQuantity("")
    setOpen(true)
  }

  const parsed = Number(quantity)
  const valid = quantity !== "" && Number.isInteger(parsed) && parsed > 0
  const canSave = Boolean(catalogVariantId) && valid && reason.trim().length > 0

  async function save() {
    if (!catalogVariantId) return
    try {
      const result = await add.mutateAsync({
        catalogVariantId,
        quantity_to_add: parsed,
        reason: reason.trim(),
      })
      toast.success(
        `Stock Added: +${parsed} boxes. New Stock: ${result?.quantity_after ?? "—"} boxes.`
      )
      setOpen(false)
    } catch (error) {
      toast.error(getApiErrorMessage(error))
    }
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <Button variant="outline" size="sm" onClick={openDialog}>
        Add Stock
      </Button>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>Add Stock — {group.name}</DialogTitle>
        </DialogHeader>
        <div className="flex flex-col gap-3">
          <p className="text-muted-foreground text-sm">
            This is the combined total across every underlying Shopify pack size. The
            individual pack sizes below stay as read-only reference — this addition is
            recorded against {group.name}&apos;s own total, not any one of them.
          </p>
          <div className="text-sm">
            Current Stock:{" "}
            <span className="font-semibold">{group.available_boxes} boxes</span>
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="quantity-to-add">Quantity to Add</Label>
            <Input
              id="quantity-to-add"
              type="number"
              min={1}
              className="w-32"
              value={quantity}
              onChange={(e) => setQuantity(e.target.value)}
              placeholder="0"
              autoFocus
            />
          </div>
          {valid && (
            <span className="font-semibold text-emerald-600">
              New Stock: {group.available_boxes + parsed} boxes
            </span>
          )}
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
          <Button disabled={!canSave || add.isPending} onClick={save}>
            {add.isPending ? "Saving..." : "Add Stock"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

/** Dispatches to the right Add Stock UX: a multi-SKU OMS variant (e.g.
 * Blue Packet) adds to its combined total as ONE number; a single-SKU
 * variant adds to that one real row directly. Never both at once.
 */
function OmsAddStockDialog({ group }: { group: OmsCatalogVariant }) {
  if (group.underlying_variant_count > 1) {
    return <MultiSkuAddStockDialog group={group} />
  }
  return <SingleSkuAddStockDialog group={group} />
}

/** Packets-per-box configuration for one underlying Shopify variant --
 * changes only the packets<->boxes conversion/display, never
 * `available_boxes` itself, and never rewrites historical movements.
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

/** How many packets/pouches ONE unit of this variant (as ordered, e.g.
 * "1" for one 120-Pack bundle purchased) actually contains -- e.g. 60
 * for a "60 Pack" SKU, 120 for a "120 Pack" SKU. Combined with Packets/
 * box above, this drives how many boxes a FUTURE dispatch/RTO deducts/
 * restores for this SKU (see `InventoryService.apply_dispatch`); it
 * never moves `available_boxes` itself or rewrites past history.
 */
function PackSizeDialog({ variant }: { variant: ProductVariantStockLine }) {
  const [open, setOpen] = React.useState(false)
  const [value, setValue] = React.useState(String(variant.pack_size))
  const update = useUpdatePackSize(variant.id)

  function openDialog() {
    setValue(String(variant.pack_size))
    setOpen(true)
  }

  const parsed = Number(value)
  const canSubmit = value !== "" && Number.isInteger(parsed) && parsed > 0

  function submit() {
    update.mutate(
      { pack_size: parsed },
      {
        onSuccess: () => {
          toast.success("Pack size updated.")
          setOpen(false)
        },
        onError: (error) => toast.error(getApiErrorMessage(error)),
      }
    )
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <Button variant="ghost" size="sm" onClick={openDialog}>
        Pack size
      </Button>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Pack size — {variant.display_title}</DialogTitle>
        </DialogHeader>
        <div className="flex flex-col gap-3">
          <p className="text-muted-foreground text-sm">
            How many packets ONE unit of this SKU contains (e.g. 120 for a &quot;120
            Pack&quot;) -- drives future shipment/RTO box math for this SKU only. The box
            count itself (
            {variant.available_boxes} boxes) and past movement history are not affected.
          </p>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="pack-size">Pack size (packets per unit)</Label>
            <Input
              id="pack-size"
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

/** The underlying Shopify variant rows for ONE OMS variant -- kept intact
 * and shown only inside a clearly secondary, collapsible section (rows,
 * never OMS-visible cards).
 */
function UnderlyingShopifyVariants({
  group,
  canManage,
  isCanonicalHerbalMasala,
}: {
  group: OmsCatalogVariant
  canManage: boolean
  isCanonicalHerbalMasala: boolean
}) {
  if (group.underlying_variant_count === 0) {
    return (
      <p className="text-muted-foreground mt-3 text-sm">
        No Shopify variants are grouped under this OMS variant yet.
      </p>
    )
  }
  return (
    <details className="mt-3">
      <summary className="text-muted-foreground cursor-pointer text-sm select-none">
        Underlying Shopify variants ({group.underlying_variant_count})
      </summary>
      <div className="mt-2 flex flex-col divide-y rounded-md border">
        {group.underlying_variants.map((v) => {
          const packSize = isCanonicalHerbalMasala ? herbalMasalaPackSize(v.sku) : null
          const label = packSize ? `${packSize} Pack` : v.display_title
          return (
            <div
              key={v.id}
              className="flex flex-wrap items-center justify-between gap-2 p-2 text-sm"
            >
              <div className="flex items-center gap-2">
                <ProductThumbnail src={v.image_url} alt={label} size="size-8" />
                <div>
                  <span className="font-medium">{label}</span>
                  <span className="text-muted-foreground">
                    {" "}
                    · SKU {v.sku} · {v.available_boxes} boxes · {v.packets_per_box}/box · pack
                    size {v.pack_size}
                  </span>
                </div>
              </div>
              {canManage && (
                <div className="flex gap-1">
                  <VariantNameEditor variant={v} />
                  <PacketsPerBoxDialog variant={v} />
                  <PackSizeDialog variant={v} />
                </div>
              )}
            </div>
          )
        })}
      </div>
    </details>
  )
}

// The one product this specific pack-size label applies to (the
// canonical, active "Aayush Wellness Herbal Masala" -- Royal Tobacco /
// Gutka / Paan Masala flavours renamed to Gold / Red / Blue Packet).
// Scoped by Shopify's own immutable product id -- never by the OMS UUID
// (differs per environment) and never by title (a draft duplicate shares
// the exact same title) -- so this can never misfire on another product.
const CANONICAL_HERBAL_MASALA_SHOPIFY_PRODUCT_ID = "8009941287101"

/** Derives "60" / "120" / "180" from this product's own real SKU shape
 * (`AW-HM-<flavour code>-<pack size>[-shopify-<id>]`, e.g.
 * `AW-HM-RG-60-shopify-45082739540157`) -- read directly off the actual
 * SKU already stored on the row, never guessed or invented. Returns
 * `null` for any SKU that doesn't match this exact shape, so callers
 * always have a safe fallback to the existing display title.
 */
function herbalMasalaPackSize(sku: string): string | null {
  const match = /^AW-HM-[A-Z]+-(\d+)(?:-shopify-\d+)?$/.exec(sku)
  return match ? match[1] : null
}

function movementTypeLabel(type: InventoryMovement["movement_type"]): string {
  return (
    INVENTORY_MOVEMENT_TYPE_OPTIONS.find((option) => option.value === type)?.label ?? type
  )
}

/** Movement history for ONE OMS variant -- spans every underlying Shopify
 * SKU grouped under it (via `catalog_variant_id`), or the single row for
 * an implicit OMS variant. No `InventoryMovement` row is rewritten.
 */
function OmsVariantHistory({
  group,
  spansMultipleSkus,
  isCanonicalHerbalMasala,
}: {
  group: OmsCatalogVariant
  spansMultipleSkus: boolean
  isCanonicalHerbalMasala: boolean
}) {
  const { page, pageSize, setPage, resetPage } = usePaginationState()
  const [movementType, setMovementType] = React.useState<
    InventoryMovementType | undefined
  >(undefined)
  const query = useInventoryMovements({
    page,
    pageSize,
    catalog_variant_id: group.catalog_variant_id ?? undefined,
    product_variant_id:
      group.catalog_variant_id === null ? group.underlying_variants[0]?.id : undefined,
    movement_type: movementType,
  })

  const columns: DataTableColumn<InventoryMovement>[] = [
    { id: "when", header: "Date/time", cell: (row) => formatDateTime(row.created_at) },
    {
      id: "sku",
      header: "Shopify SKU",
      cell: (row) => {
        if (!row.sku) return "—"
        const packSize = isCanonicalHerbalMasala ? herbalMasalaPackSize(row.sku) : null
        return packSize ? `${packSize} Pack — ${row.sku}` : row.sku
      },
    },
    {
      id: "movement",
      header: "Movement",
      cell: (row) => {
        const unit = Math.abs(row.quantity_delta) === 1 ? "box" : "boxes"
        const signed =
          row.quantity_delta > 0 ? `+${row.quantity_delta}` : `${row.quantity_delta}`
        return (
          <span className={row.quantity_delta < 0 ? "text-red-600" : "text-emerald-600"}>
            {movementTypeLabel(row.movement_type)}: {signed} {unit}
          </span>
        )
      },
    },
    { id: "balance", header: "Stock Balance", cell: (row) => `${row.quantity_after} boxes` },
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
    <div className="mt-3 flex flex-col gap-2 border-t pt-3">
      <div className="text-sm font-semibold">
        Movement History — {group.name}
        {spansMultipleSkus && (
          <span className="text-muted-foreground font-normal">
            {" "}
            (spans {group.underlying_variant_count} Shopify SKUs)
          </span>
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

/** History of Total-Stock edits for a multi-SKU OMS variant -- separate
 * from `OmsVariantHistory` above (which stays per-underlying-SKU and is
 * completely unaffected by this). Shown alongside it, never merged in.
 */
function CatalogVariantTotalAdjustmentHistory({ group }: { group: OmsCatalogVariant }) {
  const { page, pageSize, setPage } = usePaginationState()
  const catalogVariantId = group.catalog_variant_id
  const query = useCatalogVariantAdjustments(catalogVariantId ?? "", { page, pageSize })

  const columns: DataTableColumn<CatalogVariantStockAdjustment>[] = [
    { id: "when", header: "Date/time", cell: (row) => formatDateTime(row.created_at) },
    {
      id: "change",
      header: "Total stock change",
      cell: (row) => (
        <span className={row.quantity_delta < 0 ? "text-red-600" : "text-emerald-600"}>
          {row.quantity_delta > 0 ? "+" : ""}
          {row.quantity_delta} boxes
        </span>
      ),
    },
    { id: "balance", header: "Stock Balance", cell: (row) => `${row.quantity_after} boxes` },
    { id: "reason", header: "Reason", cell: (row) => row.reason },
    { id: "actor", header: "Actor/source", cell: (row) => row.actor_label },
  ]

  if (!catalogVariantId) return null

  return (
    <div className="mt-3 flex flex-col gap-2 border-t pt-3">
      <div className="text-sm font-semibold">Total Stock Adjustments — {group.name}</div>
      <QueryStates
        isLoading={query.isLoading}
        isError={query.isError}
        error={query.error}
        data={query.data}
        onRetry={() => void query.refetch()}
        isEmpty={(data) => data.data.length === 0}
        emptyTitle="No total-stock edits yet"
        emptyDescription="Edits to this variant's combined total will appear here."
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

function OmsVariantCard({
  group,
  isCanonicalHerbalMasala,
}: {
  group: OmsCatalogVariant
  isCanonicalHerbalMasala: boolean
}) {
  const { hasPermission } = useAuth()
  const canManage = hasPermission("inventory.manage")
  const [showHistory, setShowHistory] = React.useState(false)
  const spansMultipleSkus = group.underlying_variant_count > 1

  return (
    <Card>
      <CardHeader className="flex-row items-start justify-between">
        <div className="flex items-start gap-3">
          <ProductThumbnail src={group.image_url} alt={group.name} size="size-14" />
          <div>
            <CardTitle>{group.name}</CardTitle>
            <p className="text-muted-foreground text-sm">
              {group.underlying_variant_count} Shopify SKU
              {group.underlying_variant_count === 1 ? "" : "s"}
              {" · "}
              {group.packets_per_box_uniform && group.underlying_variants[0]
                ? `${group.underlying_variants[0].packets_per_box} packets/box`
                : "mixed pack sizes"}
            </p>
          </div>
        </div>
        <StockStatusBadge status={group.stock_status} />
      </CardHeader>
      <CardContent>
        <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm sm:grid-cols-4">
          <div className="col-span-2 sm:col-span-4">
            <dt className="text-muted-foreground">SKU</dt>
            {/* Shown directly on the card face -- never requires expanding
             * "Underlying Shopify variants" to find. A single-underlying-
             * variant OMS card shows its one SKU; a grouped card (e.g. a
             * future multi-pack flavour split) lists every constituent
             * SKU, comma-separated, so nothing is hidden behind a click.
             */}
            <dd className="font-mono font-medium break-words" data-testid="oms-variant-skus">
              {group.underlying_variants.map((v) => v.sku).join(", ")}
            </dd>
          </div>
          <div>
            <dt className="text-muted-foreground">Available stock</dt>
            <dd className="font-medium">
              {group.available_boxes.toLocaleString()} boxes
            </dd>
          </div>
          <div>
            <dt className="text-muted-foreground">Total packets</dt>
            <dd className="font-medium">{group.total_packets.toLocaleString()}</dd>
          </div>
          <div>
            <dt className="text-muted-foreground">Status</dt>
            <dd className="font-medium">{STOCK_STATUS_LABELS[group.stock_status]}</dd>
          </div>
        </dl>
        <div
          className="mt-4 flex flex-wrap gap-2"
          data-testid={`oms-variant-actions-${group.catalog_variant_id ?? group.underlying_variants[0]?.id ?? group.name}`}
        >
          {canManage && group.underlying_variant_count > 0 && (
            <OmsAddStockDialog group={group} />
          )}
          {canManage && <OmsVariantNameEditor group={group} />}
          <Button variant="ghost" size="sm" onClick={() => setShowHistory((s) => !s)}>
            {showHistory ? "Hide History" : "History"}
          </Button>
        </div>
        <UnderlyingShopifyVariants
          group={group}
          canManage={canManage}
          isCanonicalHerbalMasala={isCanonicalHerbalMasala}
        />
        {showHistory && (
          <OmsVariantHistory
            group={group}
            spansMultipleSkus={spansMultipleSkus}
            isCanonicalHerbalMasala={isCanonicalHerbalMasala}
          />
        )}
        {showHistory && spansMultipleSkus && <CatalogVariantTotalAdjustmentHistory group={group} />}
      </CardContent>
    </Card>
  )
}

function ProductInventory({ product }: { product: InventoryProductStock }) {
  const { hasPermission } = useAuth()
  const canManage = hasPermission("inventory.manage")
  const isCanonicalHerbalMasala =
    product.shopify_product_id === CANONICAL_HERBAL_MASALA_SHOPIFY_PRODUCT_ID

  return (
    <div className="flex flex-col gap-6">
      <div className="bg-muted/40 flex flex-wrap items-center justify-between gap-3 rounded-md border p-3 text-sm">
        <div>
          <span className="text-muted-foreground">Product total: </span>
          <span className="font-semibold">
            {product.available_boxes.toLocaleString()} boxes
          </span>
          <span className="text-muted-foreground">
            {" "}
            · {product.total_packets.toLocaleString()} packets ·{" "}
            {product.oms_variant_count} OMS variant
            {product.oms_variant_count === 1 ? "" : "s"} ·{" "}
            {product.underlying_variant_count} Shopify SKUs
          </span>
        </div>
        {canManage && (
          <ProductNameEditor
            productId={product.product_id}
            displayName={product.product_name}
            shopifyName={product.title}
            hasOverride={product.title_override !== null}
          />
        )}
      </div>

      {product.oms_variants.map((group) => (
        <OmsVariantCard
          key={group.catalog_variant_id ?? group.underlying_variants[0]?.id ?? group.name}
          group={group}
          isCanonicalHerbalMasala={isCanonicalHerbalMasala}
        />
      ))}
    </div>
  )
}

export default function InventoryProductPage() {
  const params = useParams<{ productId: string }>()
  const productId = params.productId
  const query = useInventoryProductStock(productId)

  return (
    <>
      <PageHeader
        title={query.data?.product_name ?? "Product inventory"}
        description="OMS-visible variants only. Stock is in boxes, owned by the OMS and moved only by manual edits and Shiprocket dispatch/RTO events — never by Shopify."
        backHref="/inventory"
        backLabel="Back to Inventory"
      />
      <QueryStates
        isLoading={query.isLoading}
        isError={query.isError}
        error={query.error}
        data={query.data}
        isEmpty={(data) => data.oms_variants.length === 0}
        onRetry={() => void query.refetch()}
        emptyTitle="No stock records"
        emptyDescription="This product has no variants yet."
      >
        {(data) => <ProductInventory product={data} />}
      </QueryStates>
    </>
  )
}

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
  useAdjustVariantStock,
  useInventoryMovements,
  useInventoryProductStock,
  useSetCatalogVariantName,
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

/** "Edit Stock" for ONE OMS-visible variant. Absolute target, never a
 * raw +/- delta, and NEVER a single aggregate number distributed across
 * the group: one editable row per underlying Shopify `ProductVariant`
 * (one row when the OMS variant maps 1:1). Each changed row is its own
 * `POST /inventory/stock/{id}/adjust` -> one `InventoryMovement`.
 */
function OmsEditStockDialog({ group }: { group: OmsCatalogVariant }) {
  const adjust = useAdjustVariantStock()
  const [open, setOpen] = React.useState(false)
  const [reason, setReason] = React.useState("")
  const [targets, setTargets] = React.useState<Record<string, string>>({})
  const multi = group.underlying_variant_count > 1

  function openDialog() {
    setReason("")
    setTargets(
      Object.fromEntries(
        group.underlying_variants.map((v) => [v.id, String(v.available_boxes)])
      )
    )
    setOpen(true)
  }

  const rows = group.underlying_variants.map((v) => {
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

  async function save() {
    try {
      for (const r of changedRows) {
        await adjust.mutateAsync({
          variantId: r.v.id,
          target_boxes: r.parsed,
          reason: reason.trim(),
        })
      }
      toast.success(
        changedRows.length === 1
          ? "Stock adjusted."
          : `Stock adjusted for ${changedRows.length} SKUs.`
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
          <DialogTitle>Edit Stock — {group.name}</DialogTitle>
        </DialogHeader>
        <div className="flex flex-col gap-3">
          {multi && (
            <p className="text-muted-foreground text-sm">
              This OMS variant covers several Shopify pack SKUs. Enter the new box count
              for each SKU you want to change — each edit is recorded as its own inventory
              movement. Nothing is auto-distributed.
            </p>
          )}
          <div className="flex flex-col gap-3">
            {rows.map(({ v, raw, valid, delta }) => (
              <div key={v.id} className="flex flex-col gap-1.5">
                {multi && (
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
          <Button disabled={!canSave || adjust.isPending} onClick={save}>
            {adjust.isPending ? "Saving..." : "Save"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
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
                    · SKU {v.sku} · {v.available_boxes} boxes · {v.packets_per_box}/box
                  </span>
                </div>
              </div>
              {canManage && (
                <div className="flex gap-1">
                  <VariantNameEditor variant={v} />
                  <PacketsPerBoxDialog variant={v} />
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
            <OmsEditStockDialog group={group} />
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

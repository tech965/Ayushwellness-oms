"use client"

import * as React from "react"
import { Minus, Plus } from "lucide-react"
import { toast } from "sonner"

import { QueryStates } from "@/components/shared/query-states"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { getApiErrorMessage } from "@/lib/api-client"
import { formatDateTime } from "@/lib/format"
import { useRecordPlatformStockMovement } from "@/services/platform-inventory"
import type {
  ManualPlatform,
  PlatformStockMovementType,
  PlatformStockSummaryRow,
  VariantPlatformStock,
} from "@/types/platform-inventory"

/** One entry the Add Stock / Record Sale dialog can attribute a write
 * to -- `currentStock` is THIS SKU's own real balance for the platform
 * being edited (never the product-wide aggregate shown on the card),
 * since the write always lands on one real `ProductVariant` ledger.
 */
interface VariantOption {
  id: string
  label: string
  currentStock: number
}

interface MovementDialogState {
  platform: ManualPlatform
  platformLabel: string
  direction: PlatformStockMovementType
  stockDate: string
  variantOptions: VariantOption[]
}

function platformCurrentStock(variant: VariantPlatformStock, platform: string): number {
  // Add/Record only ever render for a manual platform row, whose
  // current_stock is always a real number (never null) -- `?? 0` is a
  // defensive fallback only.
  return variant.platforms.find((p) => p.platform === platform)?.current_stock ?? 0
}

/** Sums every underlying SKU's platform row into ONE row per platform --
 * the "Marketplace Stock" table is now one table per PRODUCT, not one
 * per SKU. `opening_stock`/`current_stock` propagate `null` (never
 * silently drop it to sum only the known SKUs) if ANY contributing SKU's
 * value is `null` for that platform: a partial sum that looks complete
 * would misrepresent Shopify's historical balance as known when it
 * genuinely isn't for at least one SKU (see `PlatformStockSummaryRow`'s
 * own null-handling contract in types/platform-inventory.ts).
 * `stock_added`/`stock_deducted` are always real numbers, so those sum
 * plainly. `last_updated` is the most recent non-null timestamp seen.
 * Platform order is preserved from first encounter (the backend always
 * returns the same fixed platform set per SKU, so this is stable).
 */
function aggregatePlatformRows(variants: VariantPlatformStock[]): PlatformStockSummaryRow[] {
  const order: string[] = []
  const byPlatform = new Map<string, PlatformStockSummaryRow>()

  for (const variant of variants) {
    for (const row of variant.platforms) {
      const existing = byPlatform.get(row.platform)
      if (!existing) {
        order.push(row.platform)
        byPlatform.set(row.platform, { ...row })
        continue
      }
      existing.opening_stock =
        existing.opening_stock === null || row.opening_stock === null
          ? null
          : existing.opening_stock + row.opening_stock
      existing.stock_added += row.stock_added
      existing.stock_deducted += row.stock_deducted
      existing.current_stock =
        existing.current_stock === null || row.current_stock === null
          ? null
          : existing.current_stock + row.current_stock
      if (row.last_updated && (!existing.last_updated || row.last_updated > existing.last_updated)) {
        existing.last_updated = row.last_updated
      }
    }
  }

  return order.map((platform) => byPlatform.get(platform)!)
}

interface PlatformStockSectionProps {
  productTitle: string
  isLoading: boolean
  isError: boolean
  error: unknown
  data: { variants: VariantPlatformStock[] } | undefined
  onRetry: () => void
  canManage: boolean
  stockDate: string
}

/** "Marketplace Stock" — ONE table per PRODUCT: Shopify (automatic,
 * read-only) plus every manual platform, summed across every real
 * underlying SKU, for the selected `stockDate`. The numbers shown are
 * an aggregate (`aggregatePlatformRows`); a write always still targets
 * one specific SKU's own real ledger -- the Add Stock / Record Sale
 * dialog asks which SKU when the product has more than one.
 */
export function PlatformStockSection({
  productTitle,
  isLoading,
  isError,
  error,
  data,
  onRetry,
  canManage,
  stockDate,
}: PlatformStockSectionProps) {
  const [dialogState, setDialogState] = React.useState<MovementDialogState | null>(null)

  return (
    <Card>
      <CardHeader>
        <CardTitle>Marketplace Stock</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-6">
        <QueryStates
          isLoading={isLoading}
          isError={isError}
          error={error}
          data={data}
          onRetry={onRetry}
          isEmpty={(d) => d.variants.length === 0}
          emptyTitle="No SKUs found for this product"
        >
          {(loaded) => {
            const aggregated = aggregatePlatformRows(loaded.variants)
            return (
              <div className="flex flex-col gap-2">
                {loaded.variants.length > 1 && (
                  <p className="text-muted-foreground text-xs font-medium">
                    Combined across {loaded.variants.length} SKUs:{" "}
                    {loaded.variants.map((v) => v.sku).join(", ")}
                  </p>
                )}
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="text-muted-foreground border-border border-b text-left text-xs font-medium tracking-wide uppercase">
                        <th className="py-2 pr-3">Platform</th>
                        <th className="py-2 pr-3 text-right">Opening Stock</th>
                        <th className="py-2 pr-3 text-right">Stock Added</th>
                        <th className="py-2 pr-3 text-right">Sold / Deducted</th>
                        <th className="py-2 pr-3 text-right">Current Stock</th>
                        <th className="py-2 pr-3">Last Updated</th>
                        <th className="py-2 pr-3 text-right">Action</th>
                      </tr>
                    </thead>
                    <tbody>
                      {aggregated.map((row) => (
                        <PlatformStockTableRow
                          key={row.platform}
                          row={row}
                          canManage={canManage}
                          onAdd={() =>
                            setDialogState({
                              platform: row.platform as ManualPlatform,
                              platformLabel: row.platform_label,
                              direction: "stock_added",
                              stockDate,
                              variantOptions: loaded.variants.map((v) => ({
                                id: v.product_variant_id,
                                label: v.variant_title || v.sku,
                                currentStock: platformCurrentStock(v, row.platform),
                              })),
                            })
                          }
                          onDeduct={() =>
                            setDialogState({
                              platform: row.platform as ManualPlatform,
                              platformLabel: row.platform_label,
                              direction: "stock_deducted",
                              stockDate,
                              variantOptions: loaded.variants.map((v) => ({
                                id: v.product_variant_id,
                                label: v.variant_title || v.sku,
                                currentStock: platformCurrentStock(v, row.platform),
                              })),
                            })
                          }
                        />
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )
          }}
        </QueryStates>
      </CardContent>

      {dialogState && (
        <PlatformStockMovementDialog
          key={`${dialogState.platform}-${dialogState.direction}`}
          state={dialogState}
          productTitle={productTitle}
          onClose={() => setDialogState(null)}
        />
      )}
    </Card>
  )
}

function PlatformStockTableRow({
  row,
  canManage,
  onAdd,
  onDeduct,
}: {
  row: PlatformStockSummaryRow
  canManage: boolean
  onAdd: () => void
  onDeduct: () => void
}) {
  return (
    <tr className="border-border/60 border-b last:border-0">
      <td className="py-2 pr-3 font-medium">
        {row.platform_label}
        {row.is_automatic && (
          <Badge variant="secondary" className="ml-2 text-[10px]">
            Automatic
          </Badge>
        )}
      </td>
      <td className="py-2 pr-3 text-right tabular-nums">
        {row.opening_stock === null ? "—" : row.opening_stock}
      </td>
      <td className="py-2 pr-3 text-right tabular-nums text-emerald-600">
        {row.stock_added > 0 ? `+${row.stock_added}` : row.stock_added}
      </td>
      <td className="py-2 pr-3 text-right tabular-nums text-red-600">
        {row.stock_deducted > 0 ? `-${row.stock_deducted}` : row.stock_deducted}
      </td>
      <td className="py-2 pr-3 text-right font-semibold tabular-nums">
        {row.current_stock === null ? (
          <span className="text-muted-foreground text-xs font-normal" title="No movement recorded before this date — historical balance can't be reconstructed.">
            Not available
          </span>
        ) : (
          row.current_stock
        )}
      </td>
      <td className="text-muted-foreground py-2 pr-3 text-xs">
        {row.last_updated ? formatDateTime(row.last_updated) : "—"}
      </td>
      <td className="py-2 pr-3 text-right">
        {row.is_automatic ? (
          <span className="text-muted-foreground text-xs">Synced from Shopify</span>
        ) : canManage ? (
          <div className="flex justify-end gap-1.5">
            <Button variant="outline" size="sm" onClick={onAdd}>
              <Plus className="size-3.5" />
              Add Stock
            </Button>
            <Button variant="ghost" size="sm" onClick={onDeduct}>
              <Minus className="size-3.5" />
              Record Sale
            </Button>
          </div>
        ) : (
          <span className="text-muted-foreground text-xs">Read-only</span>
        )}
      </td>
    </tr>
  )
}

/** Add Stock / Record Sale always writes to ONE real SKU's own ledger --
 * the quantities on the card are an aggregate, but the write is not.
 * With exactly one SKU there is nothing to choose (auto-selected, no
 * picker shown, matching the previous single-SKU behavior exactly).
 * With more than one, staff must explicitly pick which SKU the movement
 * belongs to before Save enables -- never guessed, never defaulted to
 * "the first one".
 */
function PlatformStockMovementDialog({
  state,
  productTitle,
  onClose,
}: {
  state: MovementDialogState
  productTitle: string
  onClose: () => void
}) {
  const onlyOption = state.variantOptions.length === 1 ? state.variantOptions[0] : null
  const [selectedVariantId, setSelectedVariantId] = React.useState(onlyOption?.id ?? "")
  const record = useRecordPlatformStockMovement(selectedVariantId)
  const [quantity, setQuantity] = React.useState("")
  const [reason, setReason] = React.useState("")

  const selected = state.variantOptions.find((v) => v.id === selectedVariantId) ?? null
  const currentStock = selected?.currentStock ?? 0

  const parsed = Number(quantity)
  const validQuantity = quantity !== "" && Number.isInteger(parsed) && parsed > 0
  const isAdd = state.direction === "stock_added"
  const preview = isAdd
    ? currentStock + (validQuantity ? parsed : 0)
    : currentStock - (validQuantity ? parsed : 0)
  const canSave =
    Boolean(selected) && validQuantity && record.isPending === false && !(!isAdd && preview < 0)

  async function save() {
    if (!selected) return
    try {
      const result = await record.mutateAsync({
        platform: state.platform,
        movement_type: state.direction,
        quantity: parsed,
        reason: reason.trim() || undefined,
        stock_date: state.stockDate,
      })
      toast.success(
        isAdd
          ? `Stock Added: +${parsed} boxes. New Stock: ${result?.quantity_after ?? "—"} boxes.`
          : `Sale Recorded: -${parsed} boxes. New Stock: ${result?.quantity_after ?? "—"} boxes.`
      )
      onClose()
    } catch (error) {
      toast.error(getApiErrorMessage(error))
    }
  }

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>
            {isAdd ? "Add Stock" : "Record Sale"} — {state.platformLabel}
          </DialogTitle>
          <DialogDescription>
            {productTitle} · Stock Date {state.stockDate}
          </DialogDescription>
        </DialogHeader>
        <div className="flex flex-col gap-3">
          {state.variantOptions.length > 1 && (
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="platform-sku">SKU</Label>
              <Select value={selectedVariantId} onValueChange={setSelectedVariantId}>
                <SelectTrigger id="platform-sku" className="w-full">
                  <SelectValue placeholder="Select a SKU…" />
                </SelectTrigger>
                <SelectContent>
                  {state.variantOptions.map((v) => (
                    <SelectItem key={v.id} value={v.id}>
                      {v.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          )}
          <div className="text-sm">
            Current Stock:{" "}
            <span className="font-semibold">
              {selected ? `${currentStock} boxes` : "Select a SKU first"}
            </span>
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="platform-quantity">
              {isAdd ? "New stock to add" : "Quantity sold / dispatched"}
            </Label>
            <Input
              id="platform-quantity"
              type="number"
              min={1}
              className="w-32"
              value={quantity}
              onChange={(e) => setQuantity(e.target.value)}
              placeholder="0"
              autoFocus
            />
          </div>
          {selected && validQuantity && (
            <span
              className={
                isAdd
                  ? "font-semibold text-emerald-600"
                  : preview < 0
                    ? "font-semibold text-red-600"
                    : "font-semibold text-emerald-600"
              }
            >
              New Stock: {preview} boxes
            </span>
          )}
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="platform-reason">Reason (optional)</Label>
            <Input
              id="platform-reason"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder={isAdd ? "e.g. Warehouse stock received" : "e.g. Marketplace sale"}
            />
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button disabled={!canSave} onClick={save}>
            {record.isPending ? "Saving..." : isAdd ? "Add Stock" : "Record Sale"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

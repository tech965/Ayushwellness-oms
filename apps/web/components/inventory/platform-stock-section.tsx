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
import { getApiErrorMessage } from "@/lib/api-client"
import { formatDateTime } from "@/lib/format"
import { useRecordPlatformStockMovement } from "@/services/platform-inventory"
import type {
  ManualPlatform,
  PlatformStockMovementType,
  PlatformStockSummaryRow,
  VariantPlatformStock,
} from "@/types/platform-inventory"

interface MovementDialogState {
  variantId: string
  variantLabel: string
  platform: ManualPlatform
  platformLabel: string
  currentStock: number
  direction: PlatformStockMovementType
  stockDate: string
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

/** "Marketplace Stock" — one card per real Shopify SKU under this
 * product (never combined across SKUs, Requirement 11), each showing
 * Shopify (automatic, read-only) plus every manual platform for the
 * selected `stockDate`. Reuses the existing OMS card/table visual
 * language rather than a new design.
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
          {(loaded) => (
            <>
              {loaded.variants.map((variant) => (
                <div key={variant.product_variant_id} className="flex flex-col gap-2">
                  {loaded.variants.length > 1 && (
                    <p className="text-muted-foreground text-xs font-medium">
                      {variant.variant_title || variant.sku} · SKU {variant.sku}
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
                        {variant.platforms.map((row) => (
                          <PlatformStockTableRow
                            key={row.platform}
                            row={row}
                            canManage={canManage}
                            onAdd={() =>
                              setDialogState({
                                variantId: variant.product_variant_id,
                                variantLabel: variant.variant_title || variant.sku,
                                platform: row.platform as ManualPlatform,
                                platformLabel: row.platform_label,
                                // Add/Deduct only ever render for a manual
                                // platform row, whose current_stock is
                                // always a real number (never null) --
                                // the `?? 0` is a defensive fallback only.
                                currentStock: row.current_stock ?? 0,
                                direction: "stock_added",
                                stockDate,
                              })
                            }
                            onDeduct={() =>
                              setDialogState({
                                variantId: variant.product_variant_id,
                                variantLabel: variant.variant_title || variant.sku,
                                platform: row.platform as ManualPlatform,
                                platformLabel: row.platform_label,
                                currentStock: row.current_stock ?? 0,
                                direction: "stock_deducted",
                                stockDate,
                              })
                            }
                          />
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              ))}
            </>
          )}
        </QueryStates>
      </CardContent>

      {dialogState && (
        <PlatformStockMovementDialog
          key={`${dialogState.variantId}-${dialogState.platform}-${dialogState.direction}`}
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

function PlatformStockMovementDialog({
  state,
  productTitle,
  onClose,
}: {
  state: MovementDialogState
  productTitle: string
  onClose: () => void
}) {
  const record = useRecordPlatformStockMovement(state.variantId)
  const [quantity, setQuantity] = React.useState("")
  const [reason, setReason] = React.useState("")

  const parsed = Number(quantity)
  const valid = quantity !== "" && Number.isInteger(parsed) && parsed > 0
  const isAdd = state.direction === "stock_added"
  const preview = isAdd ? state.currentStock + (valid ? parsed : 0) : state.currentStock - (valid ? parsed : 0)

  async function save() {
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
            {productTitle}
            {state.variantLabel ? ` · ${state.variantLabel}` : ""} · Stock Date {state.stockDate}
          </DialogDescription>
        </DialogHeader>
        <div className="flex flex-col gap-3">
          <div className="text-sm">
            Current Stock:{" "}
            <span className="font-semibold">{state.currentStock} boxes</span>
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
          {valid && (
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
          <Button
            disabled={!valid || record.isPending || (!isAdd && preview < 0)}
            onClick={save}
          >
            {record.isPending ? "Saving..." : isAdd ? "Add Stock" : "Record Sale"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

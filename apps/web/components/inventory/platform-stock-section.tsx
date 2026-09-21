"use client"

import * as React from "react"
import { Minus, RotateCcw } from "lucide-react"
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
import { useRecordProductMarketplaceMovement } from "@/services/platform-inventory"
import type {
  ManualPlatform,
  MarketplaceOperation,
  PlatformStockSummaryRow,
} from "@/types/platform-inventory"

interface MovementDialogState {
  platform: ManualPlatform
  platformLabel: string
  direction: MarketplaceOperation
  currentStock: number
  stockDate: string
}

interface PlatformStockSectionProps {
  productId: string
  productTitle: string
  isLoading: boolean
  isError: boolean
  error: unknown
  data: { platforms: PlatformStockSummaryRow[] } | undefined
  onRetry: () => void
  canManage: boolean
  stockDate: string
}

/** "Marketplace Stock" — ONE table per PRODUCT, never one per SKU:
 * Shopify (automatic, read-only) plus every manual platform, already
 * aggregated server-side for the selected `stockDate`. Record Sale and
 * RTO are the only actions (there is no marketplace "Add Stock") and
 * both are PRODUCT-level -- no SKU is selected or shown anywhere in this
 * section. The backend converts the packet quantity entered here to
 * outers using the product's own pack_size, applies the same effect to
 * the product's OMS total stock in one transaction (see
 * `PlatformInventoryService.record_product_movement`), and rejects the
 * write (422) if that conversion isn't deterministic rather than
 * guessing. Shopify's row is automatic and never gets manual controls.
 */
export function PlatformStockSection({
  productId,
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
        <CardTitle>Marketplace Stock{productTitle ? ` — ${productTitle}` : ""}</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-6">
        <QueryStates
          isLoading={isLoading}
          isError={isError}
          error={error}
          data={data}
          onRetry={onRetry}
          isEmpty={(d) => d.platforms.length === 0}
          emptyTitle="No marketplace stock data for this product"
        >
          {(loaded) => (
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
                  {loaded.platforms.map((row) => (
                    <PlatformStockTableRow
                      key={row.platform}
                      row={row}
                      canManage={canManage}
                      onSale={() =>
                        setDialogState({
                          platform: row.platform as ManualPlatform,
                          platformLabel: row.platform_label,
                          direction: "sale",
                          currentStock: row.current_stock ?? 0,
                          stockDate,
                        })
                      }
                      onRto={() =>
                        setDialogState({
                          platform: row.platform as ManualPlatform,
                          platformLabel: row.platform_label,
                          direction: "rto",
                          currentStock: row.current_stock ?? 0,
                          stockDate,
                        })
                      }
                    />
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </QueryStates>
      </CardContent>

      {dialogState && (
        <ProductMarketplaceMovementDialog
          key={`${dialogState.platform}-${dialogState.direction}`}
          productId={productId}
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
  onSale,
  onRto,
}: {
  row: PlatformStockSummaryRow
  canManage: boolean
  onSale: () => void
  onRto: () => void
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
          <span
            className="text-muted-foreground text-xs font-normal"
            title="No movement recorded before this date — historical balance can't be reconstructed."
          >
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
          <div className="flex flex-wrap justify-end gap-1.5">
            <Button variant="outline" size="sm" onClick={onSale}>
              <Minus className="size-3.5" />
              Record Sale
            </Button>
            <Button variant="ghost" size="sm" onClick={onRto}>
              <RotateCcw className="size-3.5" />
              RTO
            </Button>
          </div>
        ) : (
          <span className="text-muted-foreground text-xs">Read-only</span>
        )}
      </td>
    </tr>
  )
}

const DIALOG_TITLES: Record<MarketplaceOperation, string> = {
  sale: "Record Sale",
  rto: "Record RTO",
}

const QUANTITY_LABELS: Record<MarketplaceOperation, string> = {
  sale: "Quantity Sold",
  rto: "Quantity Returned",
}

const QUANTITY_PLACEHOLDERS: Record<MarketplaceOperation, string> = {
  sale: "e.g. Marketplace sale",
  rto: "e.g. Customer return",
}

/** Record Sale / RTO for the whole PRODUCT on one platform -- NO SKU
 * field anywhere in this dialog. Staff enters only a packet quantity
 * and an optional reason; the resulting outer delta and both new
 * balances (this platform's, and the product's OMS total) are always
 * computed server-side, never trusted from the client. A Sale can
 * legitimately take the platform balance negative (e.g. a sale recorded
 * before that day's stock was ever added) -- never blocked, matching
 * the approved business example.
 */
function ProductMarketplaceMovementDialog({
  productId,
  state,
  productTitle,
  onClose,
}: {
  productId: string
  state: MovementDialogState
  productTitle: string
  onClose: () => void
}) {
  const record = useRecordProductMarketplaceMovement(productId)
  const [quantity, setQuantity] = React.useState("")
  const [reason, setReason] = React.useState("")

  const parsed = Number(quantity)
  const validQuantity = quantity !== "" && Number.isInteger(parsed) && parsed > 0
  const isSale = state.direction === "sale"
  const preview = isSale
    ? state.currentStock - (validQuantity ? parsed : 0)
    : state.currentStock + (validQuantity ? parsed : 0)
  const canSave = validQuantity && !record.isPending

  async function save() {
    try {
      const result = await record.mutateAsync({
        platform: state.platform,
        movement_type: state.direction,
        quantity_packets: parsed,
        reason: reason.trim() || undefined,
        stock_date: state.stockDate,
      })
      const verb = isSale ? "Sale Recorded" : "RTO Recorded"
      const sign = isSale ? "-" : "+"
      toast.success(
        `${verb}: ${sign}${parsed} packets. New Stock: ${result?.quantity_after ?? "—"} outers.`
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
            {DIALOG_TITLES[state.direction]} — {state.platformLabel}
          </DialogTitle>
          <DialogDescription>
            Product: {productTitle} · Stock Date {state.stockDate}
          </DialogDescription>
        </DialogHeader>
        <div className="flex flex-col gap-3">
          <div className="text-sm">
            Current Stock: <span className="font-semibold">{state.currentStock} outers</span>
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="marketplace-quantity">{QUANTITY_LABELS[state.direction]}</Label>
            <Input
              id="marketplace-quantity"
              type="number"
              min={1}
              className="w-32"
              value={quantity}
              onChange={(e) => setQuantity(e.target.value)}
              placeholder="0"
              autoFocus
            />
            <p className="text-muted-foreground text-xs">packets</p>
          </div>
          {validQuantity && (
            <span
              className={
                !isSale
                  ? "font-semibold text-emerald-600"
                  : preview < 0
                    ? "font-semibold text-red-600"
                    : "font-semibold text-emerald-600"
              }
            >
              New Stock: {preview} outers
            </span>
          )}
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="marketplace-reason">Reason (optional)</Label>
            <Input
              id="marketplace-reason"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder={QUANTITY_PLACEHOLDERS[state.direction]}
            />
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button disabled={!canSave} onClick={save}>
            {record.isPending ? "Saving..." : DIALOG_TITLES[state.direction]}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

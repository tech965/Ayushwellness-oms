"use client"

import * as React from "react"
import { Minus, RotateCcw } from "lucide-react"
import { toast } from "sonner"

import { MonthlySalesSummary } from "@/components/inventory/monthly-sales-summary"
import { ProductMarketplaceHistory } from "@/components/inventory/product-marketplace-history"
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
  VariantMarketplaceStock,
} from "@/types/platform-inventory"

interface MovementDialogState {
  platform: ManualPlatform
  platformLabel: string
  direction: MarketplaceOperation
  currentStock: number
  stockDate: string
  /** Set ONLY for a variant-scoped product: the OMS-visible variant
   * (Gold/Red/Blue) the movement belongs to. Never an underlying SKU.
   */
  catalogVariantId?: string
  variantName?: string
}

interface PlatformStockSectionProps {
  productId: string
  productTitle: string
  isLoading: boolean
  isError: boolean
  error: unknown
  data:
    | {
        scope: "product" | "catalog_variant"
        platforms: PlatformStockSummaryRow[]
        variants: VariantMarketplaceStock[]
      }
    | undefined
  onRetry: () => void
  canManage: boolean
  stockDate: string
}

/** "Marketplace Stock", in one of two shapes decided by the backend:
 *  - a product with fewer than two OMS-visible variants: ONE table for the
 *    product;
 *  - a product with two or more (Herbal Masala): ONE independent table PER
 *    OMS-visible variant (Gold / Red / Blue), each with its own monthly
 *    sales and history -- never combined, and never split by the
 *    underlying 60/120/180 SKUs.
 * Record Sale and RTO are the only actions (there is no marketplace "Add
 * Stock") and there is no SKU picker anywhere. The backend converts the
 * packet quantity to outers, applies the same effect to the OMS total in
 * one transaction, and rejects (422) rather than guess when the conversion
 * isn't deterministic. Shopify's row is automatic and never gets manual
 * controls.
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
  const [openHistory, setOpenHistory] = React.useState<Record<string, boolean>>({})

  const dialog = dialogState && (
    <ProductMarketplaceMovementDialog
      key={`${dialogState.catalogVariantId ?? "product"}-${dialogState.platform}-${dialogState.direction}`}
      productId={productId}
      state={dialogState}
      productTitle={productTitle}
      onClose={() => setDialogState(null)}
    />
  )

  if (data && data.scope === "catalog_variant" && data.variants.length > 0) {
    return (
      <>
        <div className="flex flex-col gap-6">
          {data.variants.map((variant) => (
            <div key={variant.catalog_variant_id} className="flex flex-col gap-3">
              <MonthlySalesSummary
                soldPackets={variant.sold_this_month_packets}
                variantName={variant.name}
              />
              <Card data-testid={`marketplace-variant-${variant.catalog_variant_id}`}>
                <CardHeader>
                  <CardTitle>Marketplace Stock — {variant.name}</CardTitle>
                </CardHeader>
                <CardContent className="flex flex-col gap-4">
                  <MarketplaceTable
                    rows={variant.platforms}
                    canManage={canManage}
                    onOperation={(row, direction) =>
                      setDialogState({
                        platform: row.platform as ManualPlatform,
                        platformLabel: row.platform_label,
                        direction,
                        currentStock: row.current_stock ?? 0,
                        stockDate,
                        catalogVariantId: variant.catalog_variant_id,
                        variantName: variant.name,
                      })
                    }
                  />
                  <div>
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() =>
                        setOpenHistory((prev) => ({
                          ...prev,
                          [variant.catalog_variant_id]: !prev[variant.catalog_variant_id],
                        }))
                      }
                    >
                      {openHistory[variant.catalog_variant_id]
                        ? `Hide ${variant.name} History`
                        : `Show ${variant.name} History`}
                    </Button>
                  </div>
                </CardContent>
              </Card>
              {openHistory[variant.catalog_variant_id] && (
                <ProductMarketplaceHistory
                  productId={productId}
                  catalogVariantId={variant.catalog_variant_id}
                  title={`${variant.name} Marketplace History`}
                />
              )}
            </div>
          ))}
        </div>
        {dialog}
      </>
    )
  }

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
          isEmpty={(d) => (d.scope === "product" ? d.platforms.length === 0 : true)}
          emptyTitle="No marketplace stock data for this product"
        >
          {(loaded) => (
            <MarketplaceTable
              rows={loaded.platforms}
              canManage={canManage}
              onOperation={(row, direction) =>
                setDialogState({
                  platform: row.platform as ManualPlatform,
                  platformLabel: row.platform_label,
                  direction,
                  currentStock: row.current_stock ?? 0,
                  stockDate,
                })
              }
            />
          )}
        </QueryStates>
      </CardContent>
      {dialog}
    </Card>
  )
}

function MarketplaceTable({
  rows,
  canManage,
  onOperation,
}: {
  rows: PlatformStockSummaryRow[]
  canManage: boolean
  onOperation: (row: PlatformStockSummaryRow, direction: MarketplaceOperation) => void
}) {
  return (
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
          {rows.map((row) => (
            <PlatformStockTableRow
              key={row.platform}
              row={row}
              canManage={canManage}
              onSale={() => onOperation(row, "sale")}
              onRto={() => onOperation(row, "rto")}
            />
          ))}
        </tbody>
      </table>
    </div>
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

/** Record Sale / RTO on one platform -- NO SKU field anywhere in this
 * dialog. Staff enters only a packet quantity and an optional reason; for
 * a variant-scoped product the movement belongs to the variant whose
 * table it was opened from (shown read-only). The resulting outer delta
 * and both new balances (this platform's, and the OMS total) are always
 * computed server-side, never trusted from the client. A Sale can
 * legitimately take the platform balance negative -- never blocked.
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
        catalog_variant_id: state.catalogVariantId,
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
            Product: {productTitle}
            {state.variantName ? ` · Variant: ${state.variantName}` : ""} · Stock Date{" "}
            {state.stockDate}
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

"use client"

import * as React from "react"
import { ChevronDown, ChevronRight, Package, Plus } from "lucide-react"
import { toast } from "sonner"

import { ErrorNote, fmt, SimpleSelect, WarehouseSelect } from "@/components/ims/common"
import { PageHeader } from "@/components/shared/page-header"
import { QueryStates } from "@/components/shared/query-states"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { getApiErrorMessage } from "@/lib/api-client"
import { useAuth } from "@/lib/auth-context"
import { newIdempotencyKey, resolveFinishedItem, useAdjust, useCategoriesOverview } from "@/services/ims"
import type { CategoryOverviewEntry, CategoryOverviewGroup, NamedLocation, PackagingOption } from "@/types/ims"

/** "Warehouse 1" + code "251" -> "Warehouse 251"; "Office" + "402" -> "Office 402".
 * The label/grouping logic lives on the backend; this only formats it for display. */
function locationHeading(loc: NamedLocation): string {
  return loc.label === "Office" ? `Office ${loc.code}` : `Warehouse ${loc.code}`
}

function ProductImage({ url, name }: { url: string | null; name: string }) {
  if (url) {
    // eslint-disable-next-line @next/next/no-img-element -- live Shopify-hosted URL, not a local asset
    return <img src={url} alt={name} className="size-9 shrink-0 rounded-md border object-cover" />
  }
  return (
    <span className="bg-muted text-muted-foreground flex size-9 shrink-0 items-center justify-center rounded-md border">
      <Package className="size-4" />
    </span>
  )
}

const TXN_TYPES = [
  { label: "Add Stock", value: "add" },
  { label: "Reduce Stock", value: "reduce" },
  { label: "Opening Stock", value: "opening" },
]

/** "Opening Stock" moves stock exactly like "Add Stock" (same
 * `adjustment_type`/`direction` sent to `/stock/adjustments` -- no
 * backend schema change) -- it exists only so the reason/remarks field
 * records WHY the stock was added, distinguishing an initial opening
 * balance from routine restocking in Inventory History without a new
 * ledger concept. */
function adjustmentTypeFor(txnType: string): "increase" | "decrease" {
  return txnType === "reduce" ? "decrease" : "increase"
}

/** Add/reduce stock for one product (a flavour or product group on the
 * Categories page). Reuses the existing `/stock/adjustments` endpoint --
 * same negative-balance guard, idempotency and ledger/history recording as
 * every other stock change in the IMS. No SKU code is ever shown; when a
 * product has more than one packaging size, "Packaging" picks which one
 * without exposing its SKU. */
function ProductStockDialog({
  group,
  onClose,
}: {
  group: CategoryOverviewGroup
  onClose: () => void
}) {
  const adjust = useAdjust()
  const options = group.packaging_options
  const [packagingSku, setPackagingSku] = React.useState<string | undefined>(options[0]?.sku)
  const [warehouse, setWarehouse] = React.useState<string>()
  const [txnType, setTxnType] = React.useState<string>("add")
  const [qty, setQty] = React.useState("")
  const [reason, setReason] = React.useState("")
  const [resolving, setResolving] = React.useState(false)
  const [key] = React.useState(newIdempotencyKey)

  const selected: PackagingOption | undefined = options.find((o) => o.sku === packagingSku)
  const n = /^\d+$/.test(qty) ? Number(qty) : 0
  const reasonOk = reason.trim().length >= 3
  const invalid = !selected || !warehouse || n <= 0 || !reasonOk || adjust.isPending || resolving

  async function submit() {
    if (!selected || !warehouse) return
    let itemId = selected.item_id
    if (!itemId) {
      if (!selected.product_id || !selected.variant_id) return
      setResolving(true)
      try {
        itemId = (await resolveFinishedItem(selected.product_id, selected.variant_id)).id
      } catch (e) {
        toast.error(getApiErrorMessage(e))
        setResolving(false)
        return
      }
      setResolving(false)
    }
    adjust.mutate(
      {
        idempotency_key: key,
        warehouse_id: warehouse,
        item_id: itemId,
        adjustment_type: adjustmentTypeFor(txnType),
        direction: txnType === "reduce" ? "out" : "in",
        quantity: n,
        reason: reason.trim(),
      },
      {
        onSuccess: (a: { reference_no: string }) => {
          toast.success(`Stock updated (${a.reference_no}).`)
          onClose()
        },
      }
    )
  }

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent onClick={(e) => e.stopPropagation()}>
        <DialogHeader>
          <DialogTitle>Add / reduce stock</DialogTitle>
          <DialogDescription>Every change is recorded in Inventory History with product, warehouse, type, quantity, reason and timestamp.</DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <div className="space-y-1">
            <Label>Product</Label>
            <p className="bg-muted/40 rounded-md border px-3 py-2 text-sm font-medium">{group.name}</p>
          </div>
          {options.length === 0 && (
            <p className="text-destructive text-sm">This product isn&apos;t linked to Shopify yet, so stock can&apos;t be recorded for it here.</p>
          )}
          {options.length > 1 && (
            <div className="space-y-1">
              <Label>Packaging</Label>
              <SimpleSelect
                value={packagingSku}
                onChange={setPackagingSku}
                options={options.map((o) => ({ label: o.packaging, value: o.sku }))}
                placeholder="Select packaging"
              />
            </div>
          )}
          <div className="space-y-1">
            <Label>Warehouse</Label>
            <WarehouseSelect activeOnly value={warehouse} onChange={setWarehouse} />
          </div>
          <div className="space-y-1">
            <Label>Transaction type</Label>
            <SimpleSelect value={txnType} onChange={(v) => v && setTxnType(v)} options={TXN_TYPES} />
          </div>
          <div className="space-y-1">
            <Label htmlFor="product-stock-qty">Quantity</Label>
            <Input id="product-stock-qty" type="number" min={1} step={1} value={qty} onChange={(e) => setQty(e.target.value)} />
          </div>
          <div className="space-y-1">
            <Label htmlFor="product-stock-reason">Reason / remarks</Label>
            <Input
              id="product-stock-reason"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder={txnType === "opening" ? "e.g. Opening balance on IMS go-live" : "e.g. Physical count correction"}
            />
          </div>
          <ErrorNote error={adjust.error} />
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button disabled={invalid} onClick={submit}>
            {adjust.isPending || resolving ? "Saving…" : "Save Stock"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function ProductRow({ group, locations }: { group: CategoryOverviewGroup; locations: NamedLocation[] }) {
  const { hasPermission } = useAuth()
  const [open, setOpen] = React.useState(false)
  const [stockOpen, setStockOpen] = React.useState(false)
  return (
    <div className="border-t">
      <div className="flex w-full items-center justify-between gap-3 px-4 py-2.5 text-left text-sm">
        <button
          type="button"
          className="hover:bg-muted/50 -mx-1 flex min-w-0 flex-1 items-center gap-2 rounded px-1 py-0.5"
          onClick={() => setOpen((o) => !o)}
          aria-expanded={open}
        >
          {open ? <ChevronDown className="text-muted-foreground size-3.5 shrink-0" /> : <ChevronRight className="text-muted-foreground size-3.5 shrink-0" />}
          <ProductImage url={group.image_url} name={group.name} />
          <span className="truncate font-medium">{group.name}</span>
        </button>
        <span className="flex shrink-0 items-center gap-3">
          <span className="text-muted-foreground tabular-nums">{fmt(group.total)} units</span>
          {hasPermission("ims.stock.write") && (
            <Button size="sm" variant="outline" onClick={() => setStockOpen(true)}>
              <Plus className="size-3.5" /> Add Stock
            </Button>
          )}
        </span>
      </div>
      {open && (
        <div className="overflow-x-auto px-4 pb-3">
          <Table>
            <TableHeader>
              <TableRow>
                {locations.map((l) => (
                  <TableHead key={l.id} className="text-right">
                    {locationHeading(l)}
                  </TableHead>
                ))}
                <TableHead className="text-right">Total Stock</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              <TableRow>
                {locations.map((l) => (
                  <TableCell key={l.id} className="text-right tabular-nums">
                    {fmt(group.by_warehouse[l.id] ?? 0)}
                  </TableCell>
                ))}
                <TableCell className="text-right font-semibold tabular-nums">{fmt(group.total)}</TableCell>
              </TableRow>
            </TableBody>
          </Table>
        </div>
      )}
      {stockOpen && <ProductStockDialog group={group} onClose={() => setStockOpen(false)} />}
    </div>
  )
}

function CategorySection({ entry, locations }: { entry: CategoryOverviewEntry; locations: NamedLocation[] }) {
  const [open, setOpen] = React.useState(false)
  return (
    <div className="bg-card overflow-hidden rounded-lg border">
      <button
        type="button"
        className="hover:bg-muted/40 flex w-full flex-wrap items-center justify-between gap-3 px-4 py-3 text-left"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
      >
        <span className="flex items-center gap-2 font-semibold">
          {open ? <ChevronDown className="size-4" /> : <ChevronRight className="size-4" />}
          {entry.category}
        </span>
        <span className="text-muted-foreground flex items-center gap-5 text-sm">
          <span>
            <span className="tabular-nums">{fmt(entry.total_products)}</span> products ·{" "}
            <span className="tabular-nums">{fmt(entry.total_skus)}</span> SKUs
          </span>
          <span className="text-foreground font-semibold tabular-nums">{fmt(entry.total_stock)} total stock</span>
        </span>
      </button>
      {open && (
        <div>
          {entry.groups.map((g) => (
            <ProductRow key={g.name} group={g} locations={locations} />
          ))}
          <div className="bg-muted/30 flex items-center justify-between border-t px-4 py-2 text-sm font-semibold">
            <span>{entry.category} total</span>
            <span className="tabular-nums">{fmt(entry.total_stock)}</span>
          </div>
        </div>
      )}
    </div>
  )
}

export default function ImsCategoriesPage() {
  const query = useCategoriesOverview()
  return (
    <div className="space-y-4">
      <PageHeader
        title="Categories"
        description="Herbal Masala, Ayurvedic Products and Nutraceutical Products — click a category, then a product or flavour, to see its stock by location."
      />
      <QueryStates
        isLoading={query.isLoading}
        isError={query.isError}
        error={query.error}
        data={query.data}
        onRetry={() => query.refetch()}
        isEmpty={(d) => d.items.length === 0}
      >
        {(d) => (
          <div className="space-y-3">
            {d.items.map((entry) => (
              <CategorySection key={entry.category} entry={entry} locations={d.locations} />
            ))}
            {d.unclassified.length > 0 && (
              <div className="bg-muted/20 rounded-lg border border-dashed p-4">
                <p className="text-sm font-medium">Not in Herbal Masala, Ayurvedic or Nutraceutical</p>
                <p className="text-muted-foreground text-xs">
                  Existing classification preserved — confirm where these belong before adding them to a category.
                </p>
                <ul className="mt-2 space-y-1 text-sm">
                  {d.unclassified.map((u) => (
                    <li key={u.sku} className="flex items-center justify-between gap-2">
                      <span>
                        {u.title} <span className="text-muted-foreground font-mono text-xs">{u.sku}</span>
                      </span>
                      <span className="text-muted-foreground text-xs">{u.current_category ?? "not found in catalogue"}</span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}
      </QueryStates>
    </div>
  )
}

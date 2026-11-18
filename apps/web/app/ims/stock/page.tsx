"use client"

import * as React from "react"
import { Plus } from "lucide-react"
import { toast } from "sonner"

import {
  ErrorNote,
  fmt,
  ItemPickerDialog,
  Pager,
  SimpleSelect,
  StockBadge,
  UnitBadge,
  useDebounced,
  WarehouseSelect,
} from "@/components/ims/common"
import { PageHeader } from "@/components/shared/page-header"
import { QueryStates } from "@/components/shared/query-states"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Table, TableBody, TableCell, TableFooter, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { Textarea } from "@/components/ui/textarea"
import { useAuth } from "@/lib/auth-context"
import { newIdempotencyKey, useCategoryOptions, useStock, useStockInward } from "@/services/ims"
import type { ImsItem } from "@/types/ims"

/** Add stock for ANY product in the catalogue -- picking an item resolves
 * (and, if needed, creates) its IMS identity row before the stock-in call,
 * so a product that has never been touched by the IMS is still eligible. */
function AddStockDialog({ onClose }: { onClose: () => void }) {
  const inward = useStockInward()
  const [item, setItem] = React.useState<ImsItem | null>(null)
  const [pick, setPick] = React.useState(false)
  const [wh, setWh] = React.useState<string>()
  const [qty, setQty] = React.useState("")
  const [reference, setReference] = React.useState("")
  const [notes, setNotes] = React.useState("")
  const [key] = React.useState(newIdempotencyKey)
  const n = /^\d+$/.test(qty) ? Number(qty) : 0
  const invalid = !item || !wh || n <= 0 || inward.isPending

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Add stock</DialogTitle>
          <DialogDescription>Record a stock receipt for any product in the catalogue. Every addition is kept in the inventory movement history.</DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <div className="space-y-1">
            <Label>Product / SKU</Label>
            <Button type="button" variant="outline" className="w-full justify-start" onClick={() => setPick(true)}>
              {item ? `${item.name}${item.sku ? ` (${item.sku})` : ""}` : "Search product or SKU…"}
            </Button>
          </div>
          <div className="space-y-1">
            <Label>Warehouse / location</Label>
            <WarehouseSelect activeOnly value={wh} onChange={setWh} />
          </div>
          <div className="space-y-1">
            <Label htmlFor="add-stock-qty">Quantity</Label>
            <Input id="add-stock-qty" type="number" min={1} step={1} value={qty} onChange={(e) => setQty(e.target.value)} />
          </div>
          <div className="space-y-1">
            <Label htmlFor="add-stock-ref">Reference number (optional)</Label>
            <Input id="add-stock-ref" value={reference} onChange={(e) => setReference(e.target.value)} maxLength={120} />
          </div>
          <div className="space-y-1">
            <Label htmlFor="add-stock-notes">Notes (optional)</Label>
            <Textarea id="add-stock-notes" value={notes} onChange={(e) => setNotes(e.target.value)} />
          </div>
          <ErrorNote error={inward.error} />
        </div>
        <DialogFooter>
          <Button
            disabled={invalid}
            onClick={() => {
              const combinedNotes = [reference.trim() ? `Ref: ${reference.trim()}` : null, notes.trim() || null]
                .filter(Boolean)
                .join(" — ")
              inward.mutate(
                {
                  idempotency_key: key,
                  warehouse_id: wh,
                  item_id: item?.id,
                  quantity: n,
                  reason: "Stock received",
                  notes: combinedNotes || null,
                },
                {
                  onSuccess: (a: { reference_no: string }) => {
                    toast.success(`Stock added (${a.reference_no}).`)
                    onClose()
                  },
                }
              )
            }}
          >
            Save stock entry
          </Button>
        </DialogFooter>
        <ItemPickerDialog open={pick} onOpenChange={setPick} onPick={setItem} warehouseId={wh} kinds={["finished"]} />
      </DialogContent>
    </Dialog>
  )
}

// Lazy initializers (not `useSearchParams`, which would force this
// otherwise-static page into a Suspense boundary) -- only needs to read
// the URL once, on first mount, so the Dashboard's category/warehouse
// cards can land directly on the matching filter.
function urlParam(name: string): string | undefined {
  return typeof window !== "undefined" ? new URLSearchParams(window.location.search).get(name) ?? undefined : undefined
}

export default function ImsStockPage() {
  const { hasPermission } = useAuth()
  const [q, setQ] = React.useState("")
  const [warehouse, setWarehouse] = React.useState<string | undefined>(() => urlParam("warehouse_id"))
  const [kind, setKind] = React.useState<string>()
  const [category, setCategory] = React.useState<string | undefined>(() => urlParam("category"))
  const [status, setStatus] = React.useState<string>()
  const [includeInactive, setIncludeInactive] = React.useState(false)
  const [page, setPage] = React.useState(1)
  const [addOpen, setAddOpen] = React.useState(false)
  const dq = useDebounced(q)
  const reset = () => setPage(1)
  const query = useStock({ q: dq, warehouse_id: warehouse, kind, category, status, include_inactive: includeInactive, page, page_size: 25 })
  const cats = useCategoryOptions()

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <PageHeader title="Stock Overview" description="Current stock per item and warehouse. Finished goods (units) and packaging (own unit) are never added together." />
        {hasPermission("ims.stock.write") && (
          <Button onClick={() => setAddOpen(true)}>
            <Plus className="size-4" /> Add stock
          </Button>
        )}
      </div>
      <div className="flex flex-wrap items-center gap-3">
        <Input className="w-64" placeholder="Search item or SKU…" value={q} onChange={(e) => { setQ(e.target.value); reset() }} />
        <div className="w-52">
          <WarehouseSelect includeAll value={warehouse} onChange={(v) => { setWarehouse(v); reset() }} />
        </div>
        <div className="w-44">
          <SimpleSelect allLabel="All types" value={kind} onChange={(v) => { setKind(v); reset() }} options={[{ label: "Finished goods", value: "finished" }, { label: "Packaging", value: "packaging" }]} />
        </div>
        <div className="w-44">
          <SimpleSelect allLabel="All categories" value={category} onChange={(v) => { setCategory(v); reset() }} options={(cats.data ?? []).map((c) => ({ label: c, value: c }))} />
        </div>
        <div className="w-40">
          <SimpleSelect allLabel="Any status" value={status} onChange={(v) => { setStatus(v); reset() }} options={[{ label: "In stock", value: "in_stock" }, { label: "Low stock", value: "low_stock" }, { label: "Out of stock", value: "out_of_stock" }]} />
        </div>
        <label className="text-muted-foreground flex items-center gap-1.5 text-sm">
          <input type="checkbox" checked={includeInactive} onChange={(e) => { setIncludeInactive(e.target.checked); reset() }} />
          Include inactive
        </label>
      </div>
      <QueryStates isLoading={query.isLoading} isError={query.isError} error={query.error} data={query.data} onRetry={() => query.refetch()} isEmpty={(d) => d.items.length === 0} emptyTitle="No stock items" emptyDescription="Items appear here once stock is recorded (opening balance, inward, return or adjustment).">
        {(d) => (
          <div className="space-y-3">
            <div className="overflow-x-auto rounded-md border bg-card">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Item</TableHead>
                    <TableHead>SKU</TableHead>
                    <TableHead>Unit</TableHead>
                    {d.warehouses.map((w) => (
                      <TableHead key={w.id} className="text-right" title={w.name}>
                        {w.code}
                      </TableHead>
                    ))}
                    <TableHead className="text-right">Total</TableHead>
                    <TableHead>Status</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {d.items.map((r) => (
                    <TableRow key={r.id} className={r.is_active ? undefined : "opacity-60"}>
                      <TableCell className="font-medium">
                        {r.name} {!r.is_active && <span className="text-muted-foreground text-xs">(inactive)</span>}
                      </TableCell>
                      <TableCell className="font-mono text-xs">{r.sku ?? "—"}</TableCell>
                      <TableCell>
                        <UnitBadge item={r} />
                      </TableCell>
                      {d.warehouses.map((w) => (
                        <TableCell key={w.id} className="text-right tabular-nums">
                          {fmt(r.by_warehouse[w.id] ?? 0)}
                        </TableCell>
                      ))}
                      <TableCell className="text-right font-semibold tabular-nums">{fmt(r.total)}</TableCell>
                      <TableCell>
                        <StockBadge status={r.status} />
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
                {category && (
                  <TableFooter>
                    <TableRow>
                      <TableCell className="font-semibold" colSpan={3}>
                        {category} total
                      </TableCell>
                      {d.warehouses.map((w) => (
                        <TableCell key={w.id} className="text-right font-semibold tabular-nums">
                          {fmt(d.totals.by_warehouse[w.id] ?? 0)}
                        </TableCell>
                      ))}
                      <TableCell className="text-right font-semibold tabular-nums">{fmt(d.totals.total)}</TableCell>
                      <TableCell />
                    </TableRow>
                  </TableFooter>
                )}
              </Table>
            </div>
            <Pager page={page} pageSize={d.page_size} total={d.total_items} onPage={setPage} />
          </div>
        )}
      </QueryStates>
      {addOpen && <AddStockDialog onClose={() => setAddOpen(false)} />}
    </div>
  )
}

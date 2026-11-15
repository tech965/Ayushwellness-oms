"use client"

import * as React from "react"
import Link from "next/link"
import { Calculator, Plus, Settings2, Trash2 } from "lucide-react"
import { toast } from "sonner"

import { ErrorNote, fmt, SimpleSelect, WarehouseSelect } from "@/components/ims/common"
import { PageHeader } from "@/components/shared/page-header"
import { QueryStates } from "@/components/shared/query-states"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { getApiErrorMessage } from "@/lib/api-client"
import { useAuth } from "@/lib/auth-context"
import {
  convertUnits,
  newIdempotencyKey,
  useCreatePackaging,
  usePackaging,
  useSetConversions,
  useStockInward,
  useUpdatePackaging,
} from "@/services/ims"
import { PACKAGING_UNITS, type PackagingProduct, type PackagingUnit } from "@/types/ims"

function UnitToggle({ value, onChange }: { value: string[]; onChange: (v: string[]) => void }) {
  return (
    <div className="flex flex-wrap gap-2">
      {PACKAGING_UNITS.map((u) => (
        <label key={u} className="flex items-center gap-1.5 rounded-md border px-2 py-1 text-sm">
          <input
            type="checkbox"
            checked={value.includes(u)}
            onChange={(e) => onChange(e.target.checked ? [...value, u] : value.filter((x) => x !== u))}
          />
          {u}
        </label>
      ))}
    </div>
  )
}

function NewProductDialog({ onClose }: { onClose: () => void }) {
  const create = useCreatePackaging()
  const [name, setName] = React.useState("")
  const [units, setUnits] = React.useState<string[]>([])
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Add packaging product</DialogTitle>
          <DialogDescription>Choose only the units this product actually uses.</DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <div className="space-y-1">
            <Label htmlFor="pk-name">Product name</Label>
            <Input id="pk-name" value={name} onChange={(e) => setName(e.target.value)} />
          </div>
          <UnitToggle value={units} onChange={setUnits} />
          <ErrorNote error={create.error} />
        </div>
        <DialogFooter>
          <Button
            disabled={!name.trim() || units.length === 0 || create.isPending}
            onClick={() => create.mutate({ name: name.trim(), units }, { onSuccess: () => { toast.success("Packaging product added."); onClose() } })}
          >
            Add
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function StockInDialog({ product, onClose }: { product: PackagingProduct; onClose: () => void }) {
  const inward = useStockInward()
  const [wh, setWh] = React.useState<string>()
  const [unit, setUnit] = React.useState<string>(product.tracked_units[0])
  const [qty, setQty] = React.useState("")
  const [reason, setReason] = React.useState("Packaging stock received")
  const [key] = React.useState(newIdempotencyKey)
  const n = /^\d+$/.test(qty) ? Number(qty) : 0
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Add stock — {product.name}</DialogTitle>
          <DialogDescription>Adds to one packaging unit only; other units are not touched.</DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <WarehouseSelect activeOnly value={wh} onChange={setWh} />
          <SimpleSelect value={unit} onChange={(v) => setUnit(v ?? unit)} options={product.tracked_units.map((u) => ({ label: u, value: u }))} />
          <Input type="number" min={1} placeholder="Quantity" value={qty} onChange={(e) => setQty(e.target.value)} />
          <Input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Reason / reference" />
          <ErrorNote error={inward.error} />
        </div>
        <DialogFooter>
          <Button
            disabled={!wh || n <= 0 || reason.trim().length < 3 || inward.isPending}
            onClick={() =>
              inward.mutate(
                { idempotency_key: key, warehouse_id: wh, item_id: product.units[unit as PackagingUnit]?.item_id, quantity: n, reason: reason.trim() },
                { onSuccess: (a) => { toast.success(`Stock added (${a.reference_no}).`); onClose() } }
              )
            }
          >
            Add stock
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function ConversionDialog({ product, onClose }: { product: PackagingProduct; onClose: () => void }) {
  const { hasPermission } = useAuth()
  const canManage = hasPermission("ims.manage")
  const save = useSetConversions()
  const addUnits = useUpdatePackaging()
  const [rules, setRules] = React.useState(product.conversions.map((c) => ({ ...c, ratio: String(c.ratio) })))
  const [from, setFrom] = React.useState<string>(product.tracked_units[0])
  const [to, setTo] = React.useState<string>(product.tracked_units[1] ?? product.tracked_units[0])
  const [qty, setQty] = React.useState("1")
  const [calc, setCalc] = React.useState<string | null>(null)

  const setRule = (i: number, patch: Partial<(typeof rules)[number]>) => setRules((r) => r.map((x, j) => (j === i ? { ...x, ...patch } : x)))
  const unitOpts = PACKAGING_UNITS.map((u) => ({ label: u, value: u }))

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-w-xl">
        <DialogHeader>
          <DialogTitle>Unit conversions — {product.name}</DialogTitle>
          <DialogDescription>
            “1 larger = N smaller”. This is a display relationship only; it never creates or consumes stock.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          {product.conversion_summary.length > 0 ? (
            product.conversion_summary.map((s) => (
              <Badge key={s} variant="secondary" className="text-sm">
                {s}
              </Badge>
            ))
          ) : (
            <p className="text-muted-foreground text-sm">No conversion configured for this product.</p>
          )}
          {canManage && (
            <div className="space-y-2">
              {rules.map((r, i) => (
                <div key={i} className="flex items-center gap-2 text-sm">
                  1
                  <div className="w-28"><SimpleSelect value={r.larger_unit} onChange={(v) => setRule(i, { larger_unit: (v ?? r.larger_unit) as PackagingUnit })} options={unitOpts} /></div>
                  =
                  <Input className="w-24" type="number" min={1} value={r.ratio} onChange={(e) => setRule(i, { ratio: e.target.value })} />
                  <div className="w-28"><SimpleSelect value={r.smaller_unit} onChange={(v) => setRule(i, { smaller_unit: (v ?? r.smaller_unit) as PackagingUnit })} options={unitOpts} /></div>
                  <Button variant="ghost" size="icon" aria-label="Remove rule" onClick={() => setRules(rules.filter((_, j) => j !== i))}>
                    <Trash2 className="size-4" />
                  </Button>
                </div>
              ))}
              <div className="flex gap-2">
                <Button variant="outline" size="sm" onClick={() => setRules([...rules, { larger_unit: "BOX", smaller_unit: "OUTER", ratio: "1" }])}>
                  <Plus className="size-4" /> Add rule
                </Button>
                <Button
                  size="sm"
                  disabled={save.isPending}
                  onClick={() =>
                    save.mutate(
                      { id: product.id, rules: rules.map((r) => ({ larger_unit: r.larger_unit, smaller_unit: r.smaller_unit, ratio: Number(r.ratio) })) },
                      { onSuccess: () => toast.success("Conversions saved."), onError: (e) => toast.error(getApiErrorMessage(e)) }
                    )
                  }
                >
                  Save conversions
                </Button>
              </div>
              <div className="pt-2">
                <p className="text-muted-foreground mb-1 text-xs">Track another unit for this product</p>
                <div className="flex flex-wrap gap-2">
                  {PACKAGING_UNITS.filter((u) => !product.tracked_units.includes(u)).map((u) => (
                    <Button key={u} size="xs" variant="outline" onClick={() => addUnits.mutate({ id: product.id, add_units: [u] }, { onSuccess: () => toast.success(`${u} is now tracked.`) })}>
                      + {u}
                    </Button>
                  ))}
                </div>
              </div>
            </div>
          )}
          <div className="rounded-md border p-3">
            <p className="mb-2 flex items-center gap-1 text-sm font-medium"><Calculator className="size-4" /> Converter</p>
            <div className="flex flex-wrap items-center gap-2">
              <Input className="w-24" type="number" min={1} value={qty} onChange={(e) => setQty(e.target.value)} />
              <div className="w-28"><SimpleSelect value={from} onChange={(v) => setFrom(v ?? from)} options={unitOpts} /></div>
              →
              <div className="w-28"><SimpleSelect value={to} onChange={(v) => setTo(v ?? to)} options={unitOpts} /></div>
              <Button
                size="sm"
                variant="outline"
                onClick={async () => {
                  try {
                    const r = await convertUnits({ packaging_product_id: product.id, from_unit: from, to_unit: to, quantity: Number(qty) })
                    setCalc(r.is_whole_number ? `${fmt(Number(qty))} ${from} = ${fmt(r.result)} ${to}` : `${fmt(Number(qty))} ${from} = ${r.result} ${to} (not a whole number: ${fmt(r.whole_result)} whole + ${fmt(r.remainder_in_from_unit)} ${from} left over)`)
                  } catch (e) {
                    setCalc(getApiErrorMessage(e))
                  }
                }}
              >
                Convert
              </Button>
            </div>
            {calc && <p className="mt-2 text-sm">{calc}</p>}
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}

export default function ImsPackagingPage() {
  const { hasPermission } = useAuth()
  const [wh, setWh] = React.useState<string>()
  const [q, setQ] = React.useState("")
  const query = usePackaging(wh, q)
  const [adding, setAdding] = React.useState(false)
  const [stockFor, setStockFor] = React.useState<PackagingProduct | null>(null)
  const [convId, setConvId] = React.useState<string | null>(null)
  const convFor = query.data?.items.find((p) => p.id === convId) ?? null

  return (
    <div className="space-y-4">
      <PageHeader
        title="Packaging Inventory"
        description="Each packaging unit is its own stock. — means the product does not use that unit."
        actions={
          hasPermission("ims.manage") && (
            <Button onClick={() => setAdding(true)}>
              <Plus className="size-4" /> Add packaging product
            </Button>
          )
        }
      />
      <div className="flex flex-wrap items-center gap-3">
        <div className="w-56"><WarehouseSelect includeAll value={wh} onChange={setWh} /></div>
        <Input className="w-56" placeholder="Search product…" value={q} onChange={(e) => setQ(e.target.value)} />
        <p className="text-muted-foreground text-xs">
          Transfer, return or adjust packaging from <Link className="underline" href="/ims/transfers">Transfers</Link>, <Link className="underline" href="/ims/returns">Returns</Link> and <Link className="underline" href="/ims/adjustments">Adjustments</Link>.
        </p>
      </div>
      <QueryStates isLoading={query.isLoading} isError={query.isError} error={query.error} data={query.data} onRetry={() => query.refetch()} isEmpty={(d) => d.items.length === 0} emptyTitle="No packaging products yet" emptyDescription="Add a packaging product and the units it uses (BOX, OUTER, SACHETS, ZIPPER, BOTTLE, JAR).">
        {(d) => (
          <div className="bg-card overflow-x-auto rounded-md border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Product</TableHead>
                  {PACKAGING_UNITS.map((u) => (
                    <TableHead key={u} className="text-right">{u}</TableHead>
                  ))}
                  <TableHead>Conversion</TableHead>
                  <TableHead />
                </TableRow>
              </TableHeader>
              <TableBody>
                {d.items.map((p) => (
                  <TableRow key={p.id}>
                    <TableCell className="font-medium">{p.name}</TableCell>
                    {PACKAGING_UNITS.map((u) => (
                      <TableCell key={u} className="text-right tabular-nums">
                        {p.units[u] ? fmt(p.units[u]!.quantity) : <span className="text-muted-foreground">—</span>}
                      </TableCell>
                    ))}
                    <TableCell className="text-muted-foreground text-xs">{p.conversion_summary.join("; ") || "—"}</TableCell>
                    <TableCell className="whitespace-nowrap text-right">
                      {hasPermission("ims.stock.write") && (
                        <Button size="sm" variant="outline" onClick={() => setStockFor(p)}>Add stock</Button>
                      )}{" "}
                      <Button size="icon" variant="ghost" aria-label={`Conversions for ${p.name}`} onClick={() => setConvId(p.id)}>
                        <Settings2 className="size-4" />
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </QueryStates>
      {adding && <NewProductDialog onClose={() => setAdding(false)} />}
      {stockFor && <StockInDialog product={stockFor} onClose={() => setStockFor(null)} />}
      {convFor && <ConversionDialog key={convFor.conversion_summary.join()} product={convFor} onClose={() => setConvId(null)} />}
    </div>
  )
}

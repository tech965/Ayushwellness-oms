"use client"

import * as React from "react"
import { Loader2, Plus, Trash2 } from "lucide-react"
import { toast } from "sonner"

import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { getApiErrorMessage } from "@/lib/api-client"
import { fmt, parseQty } from "@/lib/ims-utils"
import { cn } from "@/lib/utils"

export { fmt, parseQty }
import { resolveFinishedItem, useCatalogVariantSearch, useItemDetail, useItemSearch, useWarehouses } from "@/services/ims"
import type { ImsItem, StockStatus } from "@/types/ims"

export function useDebounced<T>(value: T, ms = 300): T {
  const [v, setV] = React.useState(value)
  React.useEffect(() => {
    const t = setTimeout(() => setV(value), ms)
    return () => clearTimeout(t)
  }, [value, ms])
  return v
}

const STATUS_STYLE: Record<string, string> = {
  in_stock: "bg-emerald-500/10 text-emerald-700 dark:text-emerald-400",
  low_stock: "bg-amber-500/15 text-amber-700 dark:text-amber-400",
  out_of_stock: "bg-red-500/10 text-red-700 dark:text-red-400",
  not_tracked: "bg-muted text-muted-foreground",
}
const STATUS_LABEL: Record<string, string> = {
  in_stock: "In stock",
  low_stock: "Low stock",
  out_of_stock: "Out of stock",
  not_tracked: "Not tracked",
}

export function StockBadge({ status }: { status: StockStatus | "not_tracked" }) {
  return <Badge className={cn("border-0", STATUS_STYLE[status])}>{STATUS_LABEL[status]}</Badge>
}

export function UnitBadge({ item }: { item: Pick<ImsItem, "kind" | "unit"> }) {
  return (
    <Badge variant="outline" className="font-mono text-[0.65rem]">
      {item.kind === "packaging" ? item.unit : "UNITS"}
    </Badge>
  )
}

export function WarehouseBadge({ code, name }: { code: string | null; name?: string | null }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-sm">
      <span className="bg-primary/10 text-primary rounded px-1.5 py-0.5 font-mono text-xs font-semibold">
        {code ?? "?"}
      </span>
      {name && <span className="text-muted-foreground">{name}</span>}
    </span>
  )
}

const ALL = "__all__"

export function WarehouseSelect({
  value,
  onChange,
  includeAll = false,
  activeOnly = false,
  placeholder = "Select warehouse",
  exclude,
  className,
}: {
  value: string | undefined
  onChange: (v: string | undefined) => void
  includeAll?: boolean
  activeOnly?: boolean
  placeholder?: string
  exclude?: string
  className?: string
}) {
  const { data } = useWarehouses(activeOnly)
  return (
    <Select value={value ?? (includeAll ? ALL : "")} onValueChange={(v) => onChange(v === ALL ? undefined : v)}>
      <SelectTrigger className={cn("w-full min-w-44", className)}>
        <SelectValue placeholder={placeholder} />
      </SelectTrigger>
      <SelectContent>
        {includeAll && <SelectItem value={ALL}>All warehouses</SelectItem>}
        {(data ?? [])
          .filter((w) => w.id !== exclude)
          .map((w) => (
            <SelectItem key={w.id} value={w.id} disabled={!w.is_active && activeOnly}>
              {w.name} ({w.code}){!w.is_active ? " · inactive" : ""}
            </SelectItem>
          ))}
      </SelectContent>
    </Select>
  )
}

export function SimpleSelect({
  value,
  onChange,
  options,
  placeholder,
  allLabel,
  className,
}: {
  value: string | undefined
  onChange: (v: string | undefined) => void
  options: { label: string; value: string }[]
  placeholder?: string
  allLabel?: string
  className?: string
}) {
  return (
    <Select value={value ?? (allLabel ? ALL : "")} onValueChange={(v) => onChange(v === ALL ? undefined : v)}>
      <SelectTrigger className={cn("w-full min-w-40", className)}>
        <SelectValue placeholder={placeholder} />
      </SelectTrigger>
      <SelectContent>
        {allLabel && <SelectItem value={ALL}>{allLabel}</SelectItem>}
        {options.map((o) => (
          <SelectItem key={o.value} value={o.value}>
            {o.label}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}

export function Pager({
  page,
  pageSize,
  total,
  onPage,
}: {
  page: number
  pageSize: number
  total: number
  onPage: (p: number) => void
}) {
  const pages = Math.max(1, Math.ceil(total / pageSize))
  return (
    <div className="flex items-center justify-between border-t pt-3 text-sm">
      <span className="text-muted-foreground">
        {total === 0 ? "No results" : `${(page - 1) * pageSize + 1}–${Math.min(page * pageSize, total)} of ${fmt(total)}`}
      </span>
      <div className="flex items-center gap-2">
        <Button size="sm" variant="outline" disabled={page <= 1} onClick={() => onPage(page - 1)}>
          Previous
        </Button>
        <span className="text-muted-foreground tabular-nums">
          {page} / {pages}
        </span>
        <Button size="sm" variant="outline" disabled={page >= pages} onClick={() => onPage(page + 1)}>
          Next
        </Button>
      </div>
    </div>
  )
}

export function ConfirmDialog({
  open,
  onOpenChange,
  title,
  description,
  confirmLabel,
  pending,
  onConfirm,
}: {
  open: boolean
  onOpenChange: (o: boolean) => void
  title: string
  description: React.ReactNode
  confirmLabel: string
  pending?: boolean
  onConfirm: () => void
}) {
  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{title}</AlertDialogTitle>
          <AlertDialogDescription asChild>
            <div className="space-y-2">{description}</div>
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={pending}>Cancel</AlertDialogCancel>
          {/* Plain button (not AlertDialogAction) so the dialog stays open until the server answers. */}
          <Button onClick={onConfirm} disabled={pending}>
            {pending && <Loader2 className="size-4 animate-spin" />}
            {confirmLabel}
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}

export function ErrorNote({ error }: { error: unknown }) {
  if (!error) return null
  return (
    <p role="alert" className="text-destructive text-sm">
      {getApiErrorMessage(error)}
    </p>
  )
}

/** Dialog that finds one inventory item: a Shopify finished-goods variant or a packaging unit. */
export function ItemPickerDialog({
  open,
  onOpenChange,
  onPick,
  warehouseId,
  kinds = ["finished", "packaging"],
}: {
  open: boolean
  onOpenChange: (o: boolean) => void
  onPick: (item: ImsItem) => void
  warehouseId?: string
  kinds?: ("finished" | "packaging")[]
}) {
  const [q, setQ] = React.useState("")
  const dq = useDebounced(q)
  const [busy, setBusy] = React.useState(false)
  const [tab, setTab] = React.useState(kinds[0])
  const finished = useCatalogVariantSearch(dq, open && tab === "finished")
  const packaging = useItemSearch(dq, "packaging", warehouseId, open && tab === "packaging")

  async function pickVariant(productId: string, variantId: string) {
    setBusy(true)
    try {
      onPick(await resolveFinishedItem(productId, variantId))
      onOpenChange(false)
    } catch (e) {
      toast.error(getApiErrorMessage(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-xl">
        <DialogHeader>
          <DialogTitle>Select item</DialogTitle>
          <DialogDescription>Finished goods come live from Shopify; packaging units are tracked separately.</DialogDescription>
        </DialogHeader>
        <Input autoFocus placeholder="Search by name or SKU…" value={q} onChange={(e) => setQ(e.target.value)} />
        <Tabs value={tab} onValueChange={(v) => setTab(v as "finished" | "packaging")}>
          <TabsList>
            {kinds.includes("finished") && <TabsTrigger value="finished">Finished goods (SKU)</TabsTrigger>}
            {kinds.includes("packaging") && <TabsTrigger value="packaging">Packaging</TabsTrigger>}
          </TabsList>
          <TabsContent value="finished" className="max-h-80 overflow-y-auto">
            {finished.isLoading && <p className="text-muted-foreground p-3 text-sm">Loading Shopify catalogue…</p>}
            {finished.isError && <ErrorNote error={finished.error} />}
            {finished.data?.length === 0 && <p className="text-muted-foreground p-3 text-sm">No matching products.</p>}
            {finished.data?.map((v) => (
              <button
                key={v.variant_id}
                disabled={busy}
                onClick={() => pickVariant(v.product_id, v.variant_id)}
                className="hover:bg-muted flex w-full items-center justify-between gap-3 rounded-md px-3 py-2 text-left text-sm"
              >
                <span>
                  <span className="block font-medium">{v.label}</span>
                  <span className="text-muted-foreground text-xs">
                    {v.sku_missing ? "No SKU" : v.sku} · {v.category}
                  </span>
                </span>
                <span className="text-muted-foreground text-xs tabular-nums">{fmt(v.total_stock)} in stock</span>
              </button>
            ))}
          </TabsContent>
          <TabsContent value="packaging" className="max-h-80 overflow-y-auto">
            {packaging.isLoading && <p className="text-muted-foreground p-3 text-sm">Loading…</p>}
            {packaging.data?.length === 0 && (
              <p className="text-muted-foreground p-3 text-sm">No packaging items. Add them under Packaging Inventory.</p>
            )}
            {packaging.data?.map((i) => (
              <button
                key={i.id}
                onClick={() => {
                  onPick(i)
                  onOpenChange(false)
                }}
                className="hover:bg-muted flex w-full items-center justify-between gap-3 rounded-md px-3 py-2 text-left text-sm"
              >
                <span className="font-medium">{i.name}</span>
                <span className="text-muted-foreground text-xs tabular-nums">
                  {fmt(i.available)} {i.unit}
                </span>
              </button>
            ))}
          </TabsContent>
        </Tabs>
      </DialogContent>
    </Dialog>
  )
}

export interface Line {
  item: ImsItem
  quantity: string
}

/** Item/quantity rows with live "available at source" figures. */
export function LinesEditor({
  lines,
  onChange,
  warehouseId,
  showAvailable = false,
  kinds,
}: {
  lines: Line[]
  onChange: (l: Line[]) => void
  warehouseId?: string
  showAvailable?: boolean
  kinds?: ("finished" | "packaging")[]
}) {
  const [open, setOpen] = React.useState(false)
  const ids = lines.map((l) => l.item.id)

  return (
    <div className="space-y-2">
      {lines.length === 0 && <p className="text-muted-foreground text-sm">No items added yet.</p>}
      {lines.map((l, idx) => (
        <div key={l.item.id} className="flex items-center gap-2 rounded-md border p-2">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2 truncate text-sm font-medium">
              {l.item.name} <UnitBadge item={l.item} />
            </div>
            <div className="text-muted-foreground text-xs">
              {l.item.sku ?? "—"}
              {showAvailable && warehouseId && <AvailableLookup warehouseId={warehouseId} itemId={l.item.id} />}
            </div>
          </div>
          <Input
            type="number"
            min={1}
            step={1}
            inputMode="numeric"
            className="w-32"
            aria-label={`Quantity for ${l.item.name}`}
            value={l.quantity}
            onChange={(e) => onChange(lines.map((x, i) => (i === idx ? { ...x, quantity: e.target.value } : x)))}
          />
          <Button
            variant="ghost"
            size="icon"
            aria-label="Remove line"
            onClick={() => onChange(lines.filter((_, i) => i !== idx))}
          >
            <Trash2 className="size-4" />
          </Button>
        </div>
      ))}
      <Button type="button" variant="outline" size="sm" onClick={() => setOpen(true)}>
        <Plus className="size-4" /> Add item
      </Button>
      <ItemPickerDialog
        open={open}
        onOpenChange={setOpen}
        warehouseId={warehouseId}
        kinds={kinds}
        onPick={(item) => {
          if (ids.includes(item.id)) toast.info("That item is already in the list.")
          else onChange([...lines, { item, quantity: "" }])
        }}
      />
    </div>
  )
}

function AvailableLookup({ warehouseId, itemId }: { warehouseId: string; itemId: string }) {
  const { data, isLoading } = useItemDetail(itemId)
  const qty = data?.by_warehouse.find((w) => w.id === warehouseId)?.quantity
  return (
    <span className="ml-2">
      · Available at source: <strong className="text-foreground tabular-nums">{isLoading ? "…" : fmt(qty ?? 0)}</strong>
    </span>
  )
}

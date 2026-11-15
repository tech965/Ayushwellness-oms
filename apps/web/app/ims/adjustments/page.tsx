"use client"

import * as React from "react"
import { toast } from "sonner"

import {
  ConfirmDialog,
  ErrorNote,
  fmt,
  ItemPickerDialog,
  Pager,
  parseQty,
  SimpleSelect,
  UnitBadge,
  WarehouseBadge,
  WarehouseSelect,
} from "@/components/ims/common"
import { PageHeader } from "@/components/shared/page-header"
import { QueryStates } from "@/components/shared/query-states"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import { useAuth } from "@/lib/auth-context"
import { formatDateTime } from "@/lib/format"
import { newIdempotencyKey, useAdjust, useAdjustments } from "@/services/ims"
import { ADJUSTMENT_TYPES, type ImsItem } from "@/types/ims"

function AdjustmentForm() {
  const adjust = useAdjust()
  const [wh, setWh] = React.useState<string>()
  const [item, setItem] = React.useState<ImsItem | null>(null)
  const [pick, setPick] = React.useState(false)
  const [type, setType] = React.useState<string>()
  const [direction, setDirection] = React.useState<string>()
  const [qty, setQty] = React.useState("")
  const [reason, setReason] = React.useState("")
  const [notes, setNotes] = React.useState("")
  const [confirm, setConfirm] = React.useState(false)
  const [key, setKey] = React.useState(newIdempotencyKey)
  const [error, setError] = React.useState<unknown>(null)

  const typeDef = ADJUSTMENT_TYPES.find((t) => t.value === type)
  const effDirection = typeDef?.direction ?? direction
  const n = parseQty(qty)
  const invalid = !wh || !item || !typeDef || !effDirection || n === null || reason.trim().length < 3

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Record stock adjustment</CardTitle>
        <p className="text-muted-foreground text-sm">Applies immediately and is kept as an immutable audit record. Mistakes are fixed with a new adjustment.</p>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid gap-3 md:grid-cols-2">
          <div className="space-y-1">
            <Label>Warehouse</Label>
            <WarehouseSelect activeOnly value={wh} onChange={setWh} />
          </div>
          <div className="space-y-1">
            <Label>Item</Label>
            <Button type="button" variant="outline" className="w-full justify-start" onClick={() => setPick(true)}>
              {item ? item.name : "Choose product / SKU / packaging…"}
            </Button>
            {item && <UnitBadge item={item} />}
          </div>
          <div className="space-y-1">
            <Label>Adjustment type</Label>
            <SimpleSelect value={type} onChange={setType} placeholder="Select type" options={ADJUSTMENT_TYPES.map((t) => ({ label: t.label, value: t.value }))} />
          </div>
          {typeDef && typeDef.direction === null && (
            <div className="space-y-1">
              <Label>Direction</Label>
              <SimpleSelect value={direction} onChange={setDirection} placeholder="Add or remove" options={[{ label: "Increase (+)", value: "in" }, { label: "Decrease (−)", value: "out" }]} />
            </div>
          )}
          <div className="space-y-1">
            <Label htmlFor="adj-qty">Quantity</Label>
            <Input id="adj-qty" type="number" min={1} step={1} value={qty} onChange={(e) => setQty(e.target.value)} />
          </div>
        </div>
        <div className="space-y-1">
          <Label htmlFor="adj-reason">Reason (required)</Label>
          <Input id="adj-reason" value={reason} onChange={(e) => setReason(e.target.value)} maxLength={500} />
        </div>
        <div className="space-y-1">
          <Label htmlFor="adj-notes">Reference / notes</Label>
          <Textarea id="adj-notes" value={notes} onChange={(e) => setNotes(e.target.value)} />
        </div>
        <ErrorNote error={error} />
        <Button disabled={invalid || adjust.isPending} onClick={() => setConfirm(true)}>
          Review & apply
        </Button>
        <ItemPickerDialog open={pick} onOpenChange={setPick} warehouseId={wh} onPick={setItem} />
        <ConfirmDialog
          open={confirm}
          onOpenChange={setConfirm}
          title="Confirm stock adjustment"
          confirmLabel="Apply adjustment"
          pending={adjust.isPending}
          description={
            <p>
              {effDirection === "out" ? "Remove" : "Add"} <strong>{fmt(n)}</strong> × {item?.name} — “{reason}”. This is permanent and audited.
            </p>
          }
          onConfirm={() =>
            adjust.mutate(
              {
                idempotency_key: key,
                warehouse_id: wh,
                item_id: item?.id,
                adjustment_type: type,
                direction: effDirection,
                quantity: n,
                reason: reason.trim(),
                notes: notes.trim() || null,
              },
              {
                onSuccess: (a) => {
                  toast.success(`Adjustment ${a.reference_no} applied.`)
                  setQty(""); setReason(""); setNotes(""); setConfirm(false); setError(null); setKey(newIdempotencyKey())
                },
                onError: (e) => { setConfirm(false); setError(e) },
              }
            )
          }
        />
      </CardContent>
    </Card>
  )
}

export default function ImsAdjustmentsPage() {
  const { hasPermission } = useAuth()
  const [wh, setWh] = React.useState<string>()
  const [type, setType] = React.useState<string>()
  const [page, setPage] = React.useState(1)
  const query = useAdjustments({ warehouse_id: wh, adjustment_type: type, page, page_size: 15 })
  return (
    <div className="space-y-6">
      <PageHeader title="Stock Adjustments" description="Increases, decreases, damaged, missing and count corrections." />
      {hasPermission("ims.stock.write") && <AdjustmentForm />}
      <div className="space-y-3">
        <h2 className="text-base font-semibold">Adjustment audit trail</h2>
        <div className="flex flex-wrap gap-3">
          <div className="w-52">
            <WarehouseSelect includeAll value={wh} onChange={(v) => { setWh(v); setPage(1) }} />
          </div>
          <div className="w-52">
            <SimpleSelect allLabel="All types" value={type} onChange={(v) => { setType(v); setPage(1) }} options={ADJUSTMENT_TYPES.map((t) => ({ label: t.label, value: t.value }))} />
          </div>
        </div>
        <QueryStates isLoading={query.isLoading} isError={query.isError} error={query.error} data={query.data} onRetry={() => query.refetch()} isEmpty={(d) => d.items.length === 0} emptyTitle="No adjustments yet">
          {(d) => (
            <div className="space-y-2">
              {d.items.map((a) => (
                <div key={a.id} className="bg-card flex flex-wrap items-center justify-between gap-2 rounded-lg border p-3 text-sm">
                  <div>
                    <div className="font-medium">
                      {a.item.name} <UnitBadge item={a.item} /> <WarehouseBadge code={a.warehouse.code} />
                    </div>
                    <div className="text-muted-foreground text-xs">
                      {a.reference_no} · {a.adjustment_type.replace("_", " ")} · “{a.reason}” · {a.created_by ?? "—"} · {formatDateTime(a.created_at)}
                    </div>
                  </div>
                  <span className={`font-semibold tabular-nums ${a.direction === "out" ? "text-red-600" : "text-emerald-600"}`}>
                    {a.direction === "out" ? "−" : "+"}
                    {fmt(a.quantity)}
                  </span>
                </div>
              ))}
              <Pager page={page} pageSize={d.page_size} total={d.total_items} onPage={setPage} />
            </div>
          )}
        </QueryStates>
      </div>
    </div>
  )
}

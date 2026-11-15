"use client"

import * as React from "react"
import { ArrowRight } from "lucide-react"
import { toast } from "sonner"

import {
  ConfirmDialog,
  ErrorNote,
  fmt,
  type Line,
  LinesEditor,
  Pager,
  parseQty,
  UnitBadge,
  useDebounced,
  WarehouseBadge,
  WarehouseSelect,
} from "@/components/ims/common"
import { PageHeader } from "@/components/shared/page-header"
import { QueryStates } from "@/components/shared/query-states"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import { getApiErrorMessage } from "@/lib/api-client"
import { useAuth } from "@/lib/auth-context"
import { formatDateTime } from "@/lib/format"
import { newIdempotencyKey, useCreateTransfer, useTransferDetail, useTransfers } from "@/services/ims"
import { MOVEMENT_LABELS, type Transfer } from "@/types/ims"

function TransferForm() {
  const create = useCreateTransfer()
  const [src, setSrc] = React.useState<string>()
  const [dst, setDst] = React.useState<string>()
  const [lines, setLines] = React.useState<Line[]>([])
  const [notes, setNotes] = React.useState("")
  const [confirm, setConfirm] = React.useState(false)
  const [key, setKey] = React.useState(newIdempotencyKey)
  const [error, setError] = React.useState<string | null>(null)

  const parsed = lines.map((l) => ({ ...l, qty: parseQty(l.quantity) }))
  const problem =
    !src || !dst
      ? "Choose source and destination warehouses."
      : src === dst
        ? "Source and destination must be different."
        : lines.length === 0
          ? "Add at least one item."
          : parsed.some((l) => l.qty === null)
            ? "Every quantity must be a whole number above zero."
            : null

  function submit() {
    create.mutate(
      {
        idempotency_key: key,
        source_warehouse_id: src,
        destination_warehouse_id: dst,
        lines: parsed.map((l) => ({ item_id: l.item.id, quantity: l.qty })),
        notes: notes.trim() || null,
      },
      {
        onSuccess: (t) => {
          toast.success(`Transfer ${t.reference_no} completed — stock updated immediately.`)
          setLines([])
          setNotes("")
          setConfirm(false)
          setError(null)
          setKey(newIdempotencyKey())
        },
        onError: (e) => {
          setConfirm(false)
          setError(getApiErrorMessage(e))
          toast.error(getApiErrorMessage(e))
        },
      }
    )
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">New transfer</CardTitle>
        <p className="text-muted-foreground text-sm">Transfers apply instantly: the source decreases and the destination increases in one atomic step. There is no receiving approval.</p>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid items-end gap-3 md:grid-cols-[1fr_auto_1fr]">
          <div className="space-y-1">
            <Label>From warehouse</Label>
            <WarehouseSelect activeOnly value={src} onChange={(v) => { setSrc(v); setLines([]) }} placeholder="Source" />
          </div>
          <ArrowRight className="text-muted-foreground mb-2 hidden size-5 md:block" />
          <div className="space-y-1">
            <Label>To warehouse</Label>
            <WarehouseSelect activeOnly value={dst} onChange={setDst} placeholder="Destination" exclude={src} />
          </div>
        </div>
        <div className="space-y-1">
          <Label>Items (finished goods SKU or packaging unit)</Label>
          {src ? (
            <LinesEditor lines={lines} onChange={setLines} warehouseId={src} showAvailable />
          ) : (
            <p className="text-muted-foreground text-sm">Select a source warehouse first.</p>
          )}
        </div>
        <div className="space-y-1">
          <Label htmlFor="tr-notes">Reference / notes (optional)</Label>
          <Textarea id="tr-notes" value={notes} onChange={(e) => setNotes(e.target.value)} maxLength={2000} />
        </div>
        {error && <ErrorNote error={new Error(error)} />}
        {problem && lines.length > 0 && <p className="text-sm text-amber-600">{problem}</p>}
        <Button disabled={Boolean(problem) || create.isPending} onClick={() => setConfirm(true)}>
          Review & transfer
        </Button>
        <ConfirmDialog
          open={confirm}
          onOpenChange={setConfirm}
          title="Confirm stock transfer"
          confirmLabel="Transfer now"
          pending={create.isPending}
          onConfirm={submit}
          description={
            <>
              <p>This moves stock immediately and cannot be edited afterwards (a mistake is corrected with another transfer or adjustment).</p>
              <ul className="list-disc pl-5">
                {parsed.map((l) => (
                  <li key={l.item.id}>
                    {fmt(l.qty)} × {l.item.name} {l.item.unit ? `(${l.item.unit})` : ""}
                  </li>
                ))}
              </ul>
            </>
          }
        />
      </CardContent>
    </Card>
  )
}

function TransferDetail({ t: listed, onClose }: { t: Transfer; onClose: () => void }) {
  const detail = useTransferDetail(listed.id)
  const t = detail.data ?? listed
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-w-xl">
        <DialogHeader>
          <DialogTitle>{t.reference_no}</DialogTitle>
        </DialogHeader>
        <div className="space-y-3 text-sm">
          <div className="flex items-center gap-2">
            <WarehouseBadge code={t.source.code} name={t.source.name} /> <ArrowRight className="size-4" />
            <WarehouseBadge code={t.destination.code} name={t.destination.name} />
          </div>
          <p className="text-muted-foreground">
            By {t.created_by ?? "—"} · {formatDateTime(t.created_at)}
          </p>
          {t.notes && <p>{t.notes}</p>}
          <div className="rounded-md border">
            {t.lines.map((l) => (
              <div key={l.item.id} className="flex items-center justify-between border-b px-3 py-2 last:border-0">
                <span className="flex items-center gap-2">
                  {l.item.name} <UnitBadge item={l.item} />
                </span>
                <span className="font-medium tabular-nums">{fmt(l.quantity)}</span>
              </div>
            ))}
          </div>
          {t.movements && (
            <div>
              <p className="mb-1 font-medium">Ledger entries</p>
              {t.movements.map((m, i) => (
                <p key={i} className="text-muted-foreground text-xs tabular-nums">
                  {MOVEMENT_LABELS[m.movement_type]}: {fmt(m.quantity_before)} → {fmt(m.quantity_after)} ({m.quantity_delta > 0 ? "+" : ""}
                  {fmt(m.quantity_delta)})
                </p>
              ))}
            </div>
          )}
        </div>
      </DialogContent>
    </Dialog>
  )
}

export default function ImsTransfersPage() {
  const { hasPermission } = useAuth()
  const [warehouse, setWarehouse] = React.useState<string>()
  const [q, setQ] = React.useState("")
  const [from, setFrom] = React.useState("")
  const [to, setTo] = React.useState("")
  const [page, setPage] = React.useState(1)
  const [open, setOpen] = React.useState<Transfer | null>(null)
  const dq = useDebounced(q)
  const query = useTransfers({ warehouse_id: warehouse, q: dq, date_from: from, date_to: to, page, page_size: 15 })

  return (
    <div className="space-y-6">
      <PageHeader title="Stock Transfers" description="Instant warehouse-to-warehouse transfers of finished goods and packaging." />
      {hasPermission("ims.stock.write") && <TransferForm />}
      <div className="space-y-3">
        <h2 className="text-base font-semibold">Transfer history</h2>
        <div className="flex flex-wrap items-end gap-3">
          <div className="w-52">
            <WarehouseSelect includeAll value={warehouse} onChange={(v) => { setWarehouse(v); setPage(1) }} />
          </div>
          <Input className="w-56" placeholder="Reference, product or SKU" value={q} onChange={(e) => { setQ(e.target.value); setPage(1) }} />
          <Input type="date" className="w-40" aria-label="From date" value={from} onChange={(e) => { setFrom(e.target.value); setPage(1) }} />
          <Input type="date" className="w-40" aria-label="To date" value={to} onChange={(e) => { setTo(e.target.value); setPage(1) }} />
        </div>
        <QueryStates isLoading={query.isLoading} isError={query.isError} error={query.error} data={query.data} onRetry={() => query.refetch()} isEmpty={(d) => d.items.length === 0} emptyTitle="No transfers yet">
          {(d) => (
            <div className="space-y-2">
              {d.items.map((t) => (
                <button key={t.id} onClick={() => setOpen(t)} className="hover:border-primary/40 bg-card flex w-full flex-wrap items-center justify-between gap-3 rounded-lg border p-3 text-left text-sm transition-colors">
                  <div>
                    <div className="font-medium">{t.reference_no}</div>
                    <div className="text-muted-foreground text-xs">
                      {formatDateTime(t.created_at)} · {t.created_by ?? "—"} · {t.lines.map((l) => l.item.name).join(", ")}
                    </div>
                  </div>
                  <div className="flex items-center gap-2">
                    <WarehouseBadge code={t.source.code} /> <ArrowRight className="size-3" /> <WarehouseBadge code={t.destination.code} />
                    <span className="ml-3 font-semibold tabular-nums">{fmt(t.total_quantity)}</span>
                  </div>
                </button>
              ))}
              <Pager page={page} pageSize={d.page_size} total={d.total_items} onPage={setPage} />
            </div>
          )}
        </QueryStates>
      </div>
      {open && <TransferDetail t={open} onClose={() => setOpen(null)} />}
    </div>
  )
}

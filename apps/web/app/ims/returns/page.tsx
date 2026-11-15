"use client"

import * as React from "react"
import { toast } from "sonner"

import {
  ConfirmDialog,
  ErrorNote,
  fmt,
  type Line,
  LinesEditor,
  Pager,
  parseQty,
  SimpleSelect,
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
import { getApiErrorMessage } from "@/lib/api-client"
import { useAuth } from "@/lib/auth-context"
import { formatDate, formatDateTime } from "@/lib/format"
import { newIdempotencyKey, useCreateParty, useCreateReturn, useParties, useReturns } from "@/services/ims"

const today = () => new Date().toISOString().slice(0, 10)

function ReturnForm() {
  const { hasPermission } = useAuth()
  const parties = useParties()
  const createParty = useCreateParty()
  const create = useCreateReturn()
  const [wh, setWh] = React.useState<string>()
  const [party, setParty] = React.useState<string>()
  const [date, setDate] = React.useState(today())
  const [doc, setDoc] = React.useState("")
  const [notes, setNotes] = React.useState("")
  const [lines, setLines] = React.useState<Line[]>([])
  const [confirm, setConfirm] = React.useState(false)
  const [key, setKey] = React.useState(newIdempotencyKey)
  const [newParty, setNewParty] = React.useState("")
  const [error, setError] = React.useState<unknown>(null)

  const parsed = lines.map((l) => ({ ...l, qty: parseQty(l.quantity) }))
  const invalid = !wh || !party || !date || lines.length === 0 || parsed.some((l) => l.qty === null)
  const partyName = parties.data?.find((p) => p.id === party)?.name

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Record external return</CardTitle>
        <p className="text-muted-foreground text-sm">Stock from an external party (e.g. Mithani) is added to the receiving warehouse immediately.</p>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid gap-3 md:grid-cols-2">
          <div className="space-y-1">
            <Label>Receiving warehouse</Label>
            <WarehouseSelect activeOnly value={wh} onChange={setWh} />
          </div>
          <div className="space-y-1">
            <Label>External party</Label>
            <SimpleSelect
              value={party}
              onChange={setParty}
              placeholder="Select party"
              options={(parties.data ?? []).filter((p) => p.is_active).map((p) => ({ label: p.name, value: p.id }))}
            />
            {hasPermission("ims.stock.write") && (
              <div className="flex gap-2 pt-1">
                <Input className="h-8" placeholder="Add new party…" value={newParty} onChange={(e) => setNewParty(e.target.value)} />
                <Button
                  size="sm"
                  variant="outline"
                  disabled={!newParty.trim() || createParty.isPending}
                  onClick={() =>
                    createParty.mutate(
                      { name: newParty.trim() },
                      { onSuccess: (p) => { setParty(p.id); setNewParty(""); toast.success(`${p.name} added.`) }, onError: (e) => toast.error(getApiErrorMessage(e)) }
                    )
                  }
                >
                  Add
                </Button>
              </div>
            )}
          </div>
          <div className="space-y-1">
            <Label htmlFor="rt-date">Return date</Label>
            <Input id="rt-date" type="date" max={today()} value={date} onChange={(e) => setDate(e.target.value)} />
          </div>
          <div className="space-y-1">
            <Label htmlFor="rt-doc">Reference / document number</Label>
            <Input id="rt-doc" value={doc} onChange={(e) => setDoc(e.target.value)} maxLength={120} />
          </div>
        </div>
        <div className="space-y-1">
          <Label>Returned items</Label>
          <LinesEditor lines={lines} onChange={setLines} />
        </div>
        <div className="space-y-1">
          <Label htmlFor="rt-notes">Notes</Label>
          <Textarea id="rt-notes" value={notes} onChange={(e) => setNotes(e.target.value)} />
        </div>
        <ErrorNote error={error} />
        <Button disabled={invalid || create.isPending} onClick={() => setConfirm(true)}>
          Review & confirm return
        </Button>
        <ConfirmDialog
          open={confirm}
          onOpenChange={setConfirm}
          title="Confirm return"
          confirmLabel="Add stock now"
          pending={create.isPending}
          description={
            <>
              <p>Stock increases immediately in the receiving warehouse. Returns cannot be edited afterwards.</p>
              <p>From: {partyName}</p>
              <ul className="list-disc pl-5">
                {parsed.map((l) => (
                  <li key={l.item.id}>
                    {fmt(l.qty)} × {l.item.name}
                  </li>
                ))}
              </ul>
            </>
          }
          onConfirm={() =>
            create.mutate(
              {
                idempotency_key: key,
                warehouse_id: wh,
                party_id: party,
                return_date: date,
                document_no: doc.trim() || null,
                notes: notes.trim() || null,
                lines: parsed.map((l) => ({ item_id: l.item.id, quantity: l.qty })),
              },
              {
                onSuccess: (r) => {
                  toast.success(`Return ${r.reference_no} recorded.`)
                  setLines([]); setDoc(""); setNotes(""); setConfirm(false); setError(null); setKey(newIdempotencyKey())
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

export default function ImsReturnsPage() {
  const { hasPermission } = useAuth()
  const [wh, setWh] = React.useState<string>()
  const [from, setFrom] = React.useState("")
  const [to, setTo] = React.useState("")
  const [page, setPage] = React.useState(1)
  const query = useReturns({ warehouse_id: wh, date_from: from, date_to: to, page, page_size: 15 })
  return (
    <div className="space-y-6">
      <PageHeader title="Stock Returns" description="Inventory returned by external parties into a warehouse." />
      {hasPermission("ims.stock.write") && <ReturnForm />}
      <div className="space-y-3">
        <h2 className="text-base font-semibold">Return history</h2>
        <div className="flex flex-wrap gap-3">
          <div className="w-52">
            <WarehouseSelect includeAll value={wh} onChange={(v) => { setWh(v); setPage(1) }} />
          </div>
          <Input type="date" className="w-40" aria-label="From date" value={from} onChange={(e) => { setFrom(e.target.value); setPage(1) }} />
          <Input type="date" className="w-40" aria-label="To date" value={to} onChange={(e) => { setTo(e.target.value); setPage(1) }} />
        </div>
        <QueryStates isLoading={query.isLoading} isError={query.isError} error={query.error} data={query.data} onRetry={() => query.refetch()} isEmpty={(d) => d.items.length === 0} emptyTitle="No returns recorded">
          {(d) => (
            <div className="space-y-2">
              {d.items.map((r) => (
                <div key={r.id} className="bg-card rounded-lg border p-3 text-sm">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div className="font-medium">
                      {r.reference_no} · {r.party?.name} → <WarehouseBadge code={r.warehouse.code} name={r.warehouse.name} />
                    </div>
                    <div className="text-muted-foreground text-xs">
                      Returned {formatDate(r.return_date)} · recorded {formatDateTime(r.created_at)} by {r.created_by ?? "—"}
                      {r.document_no ? ` · doc ${r.document_no}` : ""}
                    </div>
                  </div>
                  <ul className="text-muted-foreground mt-2 grid gap-x-6 sm:grid-cols-2">
                    {r.lines.map((l) => (
                      <li key={l.item.id} className="flex justify-between">
                        <span>{l.item.name}</span>
                        <span className="text-foreground tabular-nums">{fmt(l.quantity)}</span>
                      </li>
                    ))}
                  </ul>
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

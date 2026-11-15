"use client"

import * as React from "react"

import { fmt, Pager, SimpleSelect, UnitBadge, useDebounced, WarehouseBadge, WarehouseSelect } from "@/components/ims/common"
import { PageHeader } from "@/components/shared/page-header"
import { QueryStates } from "@/components/shared/query-states"
import { Input } from "@/components/ui/input"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { formatDateTime } from "@/lib/format"
import { useLedger } from "@/services/ims"
import { MOVEMENT_LABELS } from "@/types/ims"

export default function ImsHistoryPage() {
  const [q, setQ] = React.useState("")
  const [wh, setWh] = React.useState<string>()
  const [type, setType] = React.useState<string>()
  const [ref, setRef] = React.useState("")
  const [from, setFrom] = React.useState("")
  const [to, setTo] = React.useState("")
  const [page, setPage] = React.useState(1)
  const dq = useDebounced(q)
  const dref = useDebounced(ref)
  const reset = () => setPage(1)
  const query = useLedger({ q: dq, warehouse_id: wh, movement_type: type, reference_no: dref, date_from: from, date_to: to, page, page_size: 25 })

  return (
    <div className="space-y-4">
      <PageHeader title="Inventory History" description="Append-only ledger of every stock movement. Entries cannot be edited or deleted." />
      <div className="flex flex-wrap gap-3">
        <Input className="w-56" placeholder="Item or SKU" value={q} onChange={(e) => { setQ(e.target.value); reset() }} />
        <div className="w-52">
          <WarehouseSelect includeAll value={wh} onChange={(v) => { setWh(v); reset() }} />
        </div>
        <div className="w-48">
          <SimpleSelect allLabel="All movement types" value={type} onChange={(v) => { setType(v); reset() }} options={Object.entries(MOVEMENT_LABELS).map(([value, label]) => ({ value, label }))} />
        </div>
        <Input className="w-52" placeholder="Reference no." value={ref} onChange={(e) => { setRef(e.target.value); reset() }} />
        <Input type="date" className="w-40" aria-label="From date" value={from} onChange={(e) => { setFrom(e.target.value); reset() }} />
        <Input type="date" className="w-40" aria-label="To date" value={to} onChange={(e) => { setTo(e.target.value); reset() }} />
      </div>
      <QueryStates isLoading={query.isLoading} isError={query.isError} error={query.error} data={query.data} onRetry={() => query.refetch()} isEmpty={(d) => d.items.length === 0} emptyTitle="No movements found">
        {(d) => (
          <div className="space-y-3">
            <div className="bg-card overflow-x-auto rounded-md border">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Date</TableHead>
                    <TableHead>Type</TableHead>
                    <TableHead>Item</TableHead>
                    <TableHead>Warehouse</TableHead>
                    <TableHead className="text-right">Change</TableHead>
                    <TableHead className="text-right">Before → After</TableHead>
                    <TableHead>Reference</TableHead>
                    <TableHead>User</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {d.items.map((m) => (
                    <TableRow key={m.id}>
                      <TableCell className="whitespace-nowrap">{formatDateTime(m.occurred_at)}</TableCell>
                      <TableCell>{MOVEMENT_LABELS[m.movement_type] ?? m.movement_type}</TableCell>
                      <TableCell>
                        <div className="flex items-center gap-2">
                          {m.item.name} <UnitBadge item={m.item} />
                        </div>
                      </TableCell>
                      <TableCell>
                        <WarehouseBadge code={m.warehouse.code} />
                      </TableCell>
                      <TableCell className={`text-right font-medium tabular-nums ${m.quantity_delta < 0 ? "text-red-600" : "text-emerald-600"}`}>
                        {m.quantity_delta > 0 ? "+" : ""}
                        {fmt(m.quantity_delta)}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">
                        {fmt(m.quantity_before)} → {fmt(m.quantity_after)}
                      </TableCell>
                      <TableCell className="font-mono text-xs">{m.reference_no}</TableCell>
                      <TableCell>{m.actor ?? "—"}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
            <Pager page={page} pageSize={d.page_size} total={d.total_items} onPage={setPage} />
          </div>
        )}
      </QueryStates>
    </div>
  )
}

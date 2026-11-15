"use client"

import * as React from "react"

import { fmt, Pager, SimpleSelect, StockBadge, UnitBadge, useDebounced, WarehouseSelect } from "@/components/ims/common"
import { PageHeader } from "@/components/shared/page-header"
import { QueryStates } from "@/components/shared/query-states"
import { Input } from "@/components/ui/input"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { useCategoryOptions, useStock } from "@/services/ims"

export default function ImsStockPage() {
  const [q, setQ] = React.useState("")
  const [warehouse, setWarehouse] = React.useState<string>()
  const [kind, setKind] = React.useState<string>()
  const [category, setCategory] = React.useState<string>()
  const [status, setStatus] = React.useState<string>()
  const [page, setPage] = React.useState(1)
  const dq = useDebounced(q)
  const reset = () => setPage(1)
  const query = useStock({ q: dq, warehouse_id: warehouse, kind, category, status, page, page_size: 25 })
  const cats = useCategoryOptions()

  return (
    <div className="space-y-4">
      <PageHeader title="Stock Overview" description="Current stock per item and warehouse. Finished goods (units) and packaging (own unit) are never added together." />
      <div className="flex flex-wrap gap-3">
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
                    <TableRow key={r.id}>
                      <TableCell className="font-medium">{r.name}</TableCell>
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
              </Table>
            </div>
            <Pager page={page} pageSize={d.page_size} total={d.total_items} onPage={setPage} />
          </div>
        )}
      </QueryStates>
    </div>
  )
}

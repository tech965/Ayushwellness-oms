"use client"

import * as React from "react"
import { Pencil, Plus } from "lucide-react"
import { toast } from "sonner"

import { ErrorNote, fmt, Pager, StockBadge, UnitBadge, WarehouseBadge } from "@/components/ims/common"
import { PageHeader } from "@/components/shared/page-header"
import { QueryStates } from "@/components/shared/query-states"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Textarea } from "@/components/ui/textarea"
import { getApiErrorMessage } from "@/lib/api-client"
import { useAuth } from "@/lib/auth-context"
import { formatDateTime } from "@/lib/format"
import { useCreateWarehouse, useLedger, useStock, useUpdateWarehouse, useWarehouses } from "@/services/ims"
import { MOVEMENT_LABELS, type ImsWarehouse } from "@/types/ims"

function WarehouseForm({ initial, onClose }: { initial: ImsWarehouse | null; onClose: () => void }) {
  const create = useCreateWarehouse()
  const update = useUpdateWarehouse()
  const [form, setForm] = React.useState({
    name: initial?.name ?? "",
    code: initial?.code ?? "",
    address: initial?.address ?? "",
    description: initial?.description ?? "",
    is_active: initial?.is_active ?? true,
  })
  const mut = initial ? update : create
  const set = (k: string, v: string | boolean) => setForm((f) => ({ ...f, [k]: v }))

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{initial ? "Edit warehouse" : "Add warehouse"}</DialogTitle>
        </DialogHeader>
        <div className="space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1">
              <Label htmlFor="wh-name">Name</Label>
              <Input id="wh-name" value={form.name} onChange={(e) => set("name", e.target.value)} />
            </div>
            <div className="space-y-1">
              <Label htmlFor="wh-code">Code</Label>
              <Input id="wh-code" value={form.code} onChange={(e) => set("code", e.target.value)} />
            </div>
          </div>
          <div className="space-y-1">
            <Label htmlFor="wh-address">Address</Label>
            <Input id="wh-address" value={form.address} onChange={(e) => set("address", e.target.value)} />
          </div>
          <div className="space-y-1">
            <Label htmlFor="wh-desc">Description</Label>
            <Textarea id="wh-desc" value={form.description} onChange={(e) => set("description", e.target.value)} />
          </div>
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={form.is_active} onChange={(e) => set("is_active", e.target.checked)} />
            Active (can receive and send stock)
          </label>
          <ErrorNote error={mut.error} />
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button
            disabled={mut.isPending || !form.name.trim() || !form.code.trim()}
            onClick={() => {
              const body = { ...form, name: form.name.trim(), code: form.code.trim() }
              const done = { onSuccess: () => { toast.success("Warehouse saved."); onClose() }, onError: (e: unknown) => toast.error(getApiErrorMessage(e)) }
              if (initial) update.mutate({ id: initial.id, ...body }, done)
              else create.mutate(body, done)
            }}
          >
            Save
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function WarehouseDetail({ w }: { w: ImsWarehouse }) {
  const [page, setPage] = React.useState(1)
  const stock = useStock({ warehouse_id: w.id, page_size: 200 })
  const history = useLedger({ warehouse_id: w.id, page, page_size: 10 })
  const rows = (stock.data?.items ?? []).filter((r) => (r.by_warehouse[w.id] ?? 0) > 0)

  return (
    <Tabs defaultValue="finished" className="mt-4">
      <TabsList>
        <TabsTrigger value="finished">Products / SKUs</TabsTrigger>
        <TabsTrigger value="packaging">Packaging</TabsTrigger>
        <TabsTrigger value="activity">Activity</TabsTrigger>
      </TabsList>
      {(["finished", "packaging"] as const).map((kind) => (
        <TabsContent key={kind} value={kind}>
          <QueryStates isLoading={stock.isLoading} isError={stock.isError} error={stock.error} data={stock.data} onRetry={() => stock.refetch()}>
            {() => {
              const list = rows.filter((r) => r.kind === kind)
              return list.length === 0 ? (
                <p className="text-muted-foreground py-6 text-center text-sm">No {kind === "finished" ? "finished-goods" : "packaging"} stock in this warehouse.</p>
              ) : (
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Item</TableHead>
                      <TableHead>{kind === "finished" ? "SKU" : "Unit"}</TableHead>
                      <TableHead className="text-right">Quantity</TableHead>
                      <TableHead>Status</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {list.map((r) => (
                      <TableRow key={r.id}>
                        <TableCell>{r.name}</TableCell>
                        <TableCell>{kind === "finished" ? r.sku : <UnitBadge item={r} />}</TableCell>
                        <TableCell className="text-right tabular-nums">{fmt(r.by_warehouse[w.id])}</TableCell>
                        <TableCell>
                          <StockBadge status={r.status} />
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              )
            }}
          </QueryStates>
        </TabsContent>
      ))}
      <TabsContent value="activity">
        <QueryStates isLoading={history.isLoading} isError={history.isError} error={history.error} data={history.data} onRetry={() => history.refetch()} isEmpty={(d) => d.items.length === 0} emptyTitle="No activity yet">
          {(d) => (
            <div className="space-y-2">
              {d.items.map((m) => (
                <div key={m.id} className="flex items-center justify-between border-b py-2 text-sm">
                  <div>
                    <span className="font-medium">{MOVEMENT_LABELS[m.movement_type]}</span> · {m.item.name}
                    <div className="text-muted-foreground text-xs">
                      {formatDateTime(m.occurred_at)} · {m.actor ?? "system"} · {m.reference_no}
                    </div>
                  </div>
                  <span className={`tabular-nums ${m.quantity_delta < 0 ? "text-red-600" : "text-emerald-600"}`}>
                    {m.quantity_delta > 0 ? "+" : ""}
                    {fmt(m.quantity_delta)}
                  </span>
                </div>
              ))}
              <Pager page={page} pageSize={10} total={d.total_items} onPage={setPage} />
            </div>
          )}
        </QueryStates>
      </TabsContent>
    </Tabs>
  )
}

export default function ImsWarehousesPage() {
  const { hasPermission } = useAuth()
  const canManage = hasPermission("ims.manage")
  const query = useWarehouses()
  const [editing, setEditing] = React.useState<ImsWarehouse | null | "new">(null)
  const [openId, setOpenId] = React.useState<string | null>(null)

  return (
    <div className="space-y-4">
      <PageHeader
        title="Warehouses"
        description="Inventory locations. Names and codes are configuration — Warehouse 4 stays unconfigured until its details are supplied."
        actions={
          canManage && (
            <Button onClick={() => setEditing("new")}>
              <Plus className="size-4" /> Add warehouse
            </Button>
          )
        }
      />
      <QueryStates isLoading={query.isLoading} isError={query.isError} error={query.error} data={query.data} onRetry={() => query.refetch()} isEmpty={(d) => d.length === 0}>
        {(list) => (
          <div className="space-y-4">
            {list.map((w) => (
              <Card key={w.id} className={w.is_active ? "" : "opacity-80"}>
                <CardHeader className="flex-row items-start justify-between">
                  <div className="space-y-1">
                    <CardTitle className="flex items-center gap-2 text-base">
                      {w.name} <WarehouseBadge code={w.code} />
                      {!w.is_active && <Badge variant="outline">Inactive</Badge>}
                    </CardTitle>
                    <p className="text-muted-foreground text-sm">{w.address || w.description || "No address or description yet."}</p>
                  </div>
                  <div className="flex gap-2">
                    <Button variant="outline" size="sm" onClick={() => setOpenId(openId === w.id ? null : w.id)}>
                      {openId === w.id ? "Hide stock" : "View stock & activity"}
                    </Button>
                    {canManage && (
                      <Button variant="ghost" size="icon" aria-label={`Edit ${w.name}`} onClick={() => setEditing(w)}>
                        <Pencil className="size-4" />
                      </Button>
                    )}
                  </div>
                </CardHeader>
                {openId === w.id && (
                  <CardContent>
                    <WarehouseDetail w={w} />
                  </CardContent>
                )}
              </Card>
            ))}
          </div>
        )}
      </QueryStates>
      {editing && <WarehouseForm initial={editing === "new" ? null : editing} onClose={() => setEditing(null)} />}
    </div>
  )
}

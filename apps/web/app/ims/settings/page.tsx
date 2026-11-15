"use client"

import * as React from "react"
import { RefreshCw } from "lucide-react"
import { toast } from "sonner"

import { ConfirmDialog, ErrorNote, type Line, LinesEditor, parseQty, WarehouseSelect } from "@/components/ims/common"
import { PageHeader } from "@/components/shared/page-header"
import { QueryStates } from "@/components/shared/query-states"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { getApiErrorMessage } from "@/lib/api-client"
import { useAuth } from "@/lib/auth-context"
import { useCreateParty, useOpeningBalances, useParties, useRefreshCatalog, useUpdateParty } from "@/services/ims"

function Parties() {
  const query = useParties()
  const create = useCreateParty()
  const update = useUpdateParty()
  const [name, setName] = React.useState("")
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">External parties</CardTitle>
        <CardDescription>Companies stock can be returned by (e.g. Mithani). Parties are not warehouses.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="flex gap-2">
          <Input placeholder="New party name" value={name} onChange={(e) => setName(e.target.value)} />
          <Button
            disabled={!name.trim() || create.isPending}
            onClick={() => create.mutate({ name: name.trim() }, { onSuccess: () => setName(""), onError: (e) => toast.error(getApiErrorMessage(e)) })}
          >
            Add
          </Button>
        </div>
        <QueryStates isLoading={query.isLoading} isError={query.isError} error={query.error} data={query.data} onRetry={() => query.refetch()} isEmpty={(d) => d.length === 0} emptyTitle="No parties yet">
          {(list) => (
            <div>
              {list.map((p) => (
                <div key={p.id} className="flex items-center justify-between border-b py-2 text-sm last:border-0">
                  <span>
                    {p.name} {!p.is_active && <Badge variant="outline">Inactive</Badge>}
                  </span>
                  <Button size="sm" variant="ghost" onClick={() => update.mutate({ id: p.id, is_active: !p.is_active })}>
                    {p.is_active ? "Deactivate" : "Activate"}
                  </Button>
                </div>
              ))}
            </div>
          )}
        </QueryStates>
      </CardContent>
    </Card>
  )
}

function Sync() {
  const refresh = useRefreshCatalog()
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Shopify catalogue</CardTitle>
        <CardDescription>
          Products, variants, SKUs and categories are read live from Shopify through the OMS server (credentials never reach the browser). A refresh only updates names/SKUs of items already tracked; it never changes stock.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-2">
        <Button
          variant="outline"
          disabled={refresh.isPending}
          onClick={() => refresh.mutate(undefined, { onError: (e) => toast.error(getApiErrorMessage(e)) })}
        >
          <RefreshCw className={`size-4 ${refresh.isPending ? "animate-spin" : ""}`} /> Refresh now
        </Button>
        {refresh.data && (
          <p className="text-sm">
            {refresh.data.products} products fetched · {refresh.data.labels_updated} labels updated · {refresh.data.not_found_in_shopify} tracked items no longer in Shopify
            {refresh.data.truncated ? " · catalogue truncated" : ""}
          </p>
        )}
      </CardContent>
    </Card>
  )
}

function OpeningBalances() {
  const load = useOpeningBalances()
  const [wh, setWh] = React.useState<string>()
  const [asOf, setAsOf] = React.useState("")
  const [lines, setLines] = React.useState<Line[]>([])
  const [confirm, setConfirm] = React.useState(false)
  const [error, setError] = React.useState<unknown>(null)
  const parsed = lines.map((l) => ({ ...l, qty: parseQty(l.quantity) }))
  const invalid = !wh || !asOf || lines.length === 0 || parsed.some((l) => l.qty === null)
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Load verified opening balances</CardTitle>
        <CardDescription>
          Only for items with no history in that warehouse. Nothing is imported automatically — enter only quantities you have verified.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="grid gap-3 md:grid-cols-2">
          <div className="space-y-1"><Label>Warehouse</Label><WarehouseSelect activeOnly value={wh} onChange={setWh} /></div>
          <div className="space-y-1"><Label htmlFor="ob-date">Balance date</Label><Input id="ob-date" type="date" max={new Date().toISOString().slice(0, 10)} value={asOf} onChange={(e) => setAsOf(e.target.value)} /></div>
        </div>
        <LinesEditor lines={lines} onChange={setLines} />
        <ErrorNote error={error} />
        <Button disabled={invalid || load.isPending} onClick={() => setConfirm(true)}>Review & load</Button>
        <ConfirmDialog
          open={confirm}
          onOpenChange={setConfirm}
          title="Load opening balances?"
          confirmLabel="Load balances"
          pending={load.isPending}
          description={<p>{lines.length} line(s) will be written to the ledger as of {asOf}. This cannot be undone; later corrections use adjustments.</p>}
          onConfirm={() =>
            load.mutate(
              { as_of: asOf, lines: parsed.map((l) => ({ warehouse_id: wh, item_id: l.item.id, quantity: l.qty })) },
              {
                onSuccess: (r) => { toast.success(`Opening balances loaded (${r.reference_no}).`); setLines([]); setConfirm(false); setError(null) },
                onError: (e) => { setConfirm(false); setError(e) },
              }
            )
          }
        />
      </CardContent>
    </Card>
  )
}

export default function ImsSettingsPage() {
  const { hasPermission } = useAuth()
  // UX guard only; every settings API is also permission-checked server-side.
  if (!hasPermission("ims.manage")) {
    return (
      <div className="space-y-2">
        <PageHeader title="Settings" />
        <p className="text-muted-foreground text-sm">Inventory settings need the Inventory Manager role.</p>
      </div>
    )
  }
  return (
    <div className="space-y-6">
      <PageHeader title="Settings" description="Inventory configuration. Warehouses are edited under Warehouses; unit conversions under Packaging Inventory." />
      <Sync />
      <Parties />
      <OpeningBalances />
    </div>
  )
}

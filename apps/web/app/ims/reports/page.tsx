"use client"

import * as React from "react"
import { Download } from "lucide-react"
import { toast } from "sonner"

import { fmt, SimpleSelect, useDebounced, WarehouseSelect } from "@/components/ims/common"
import { PageHeader } from "@/components/shared/page-header"
import { QueryStates } from "@/components/shared/query-states"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { getApiErrorMessage } from "@/lib/api-client"
import { downloadReport, useCategoryOptions, useReport } from "@/services/ims"

const REPORTS = [
  { value: "stock", label: "Stock" },
  { value: "movements", label: "Stock movements" },
  { value: "transfers", label: "Transfers" },
  { value: "returns", label: "Returns" },
  { value: "adjustments", label: "Adjustments" },
]
const VIEWS = [
  { value: "warehouse", label: "Warehouse-wise" },
  { value: "product", label: "Product-wise" },
  { value: "sku", label: "SKU-wise (finished goods)" },
  { value: "packaging", label: "Packaging-wise" },
  { value: "category", label: "Category-wise" },
]

export default function ImsReportsPage() {
  const [name, setName] = React.useState("stock")
  const [view, setView] = React.useState("warehouse")
  const [asOf, setAsOf] = React.useState(() => new Date().toISOString().slice(0, 10))
  const [wh, setWh] = React.useState<string>()
  const [category, setCategory] = React.useState<string>()
  const [q, setQ] = React.useState("")
  const [from, setFrom] = React.useState("")
  const [to, setTo] = React.useState("")
  const dq = useDebounced(q)
  const cats = useCategoryOptions()
  const [downloading, setDownloading] = React.useState(false)

  const params = {
    view: name === "stock" ? view : undefined,
    as_of: name === "stock" ? asOf : undefined,
    warehouse_id: wh,
    category: name === "stock" || name === "movements" ? category : undefined,
    q: name === "stock" || name === "movements" || name === "transfers" ? dq : undefined,
    date_from: name === "stock" ? undefined : from,
    date_to: name === "stock" ? undefined : to,
  }
  const query = useReport(name, params)

  return (
    <div className="space-y-4">
      <PageHeader
        title="Reports"
        description="Stock reports are rebuilt from the ledger, so any past date gives the true historical position."
        actions={
          <Button
            variant="outline"
            disabled={downloading}
            onClick={async () => {
              setDownloading(true)
              try {
                await downloadReport(name, params)
              } catch (e) {
                toast.error(getApiErrorMessage(e))
              } finally {
                setDownloading(false)
              }
            }}
          >
            <Download className="size-4" /> Export Excel
          </Button>
        }
      />
      <Tabs value={name} onValueChange={setName}>
        <TabsList>
          {REPORTS.map((r) => (
            <TabsTrigger key={r.value} value={r.value}>{r.label}</TabsTrigger>
          ))}
        </TabsList>
      </Tabs>
      <div className="flex flex-wrap items-end gap-3">
        {name === "stock" ? (
          <>
            <div className="w-56"><SimpleSelect value={view} onChange={(v) => setView(v ?? "warehouse")} options={VIEWS} /></div>
            <label className="text-muted-foreground text-xs">As of date<Input type="date" className="w-40" max={new Date().toISOString().slice(0, 10)} value={asOf} onChange={(e) => setAsOf(e.target.value)} /></label>
          </>
        ) : (
          <>
            <label className="text-muted-foreground text-xs">From<Input type="date" className="w-40" value={from} onChange={(e) => setFrom(e.target.value)} /></label>
            <label className="text-muted-foreground text-xs">To<Input type="date" className="w-40" value={to} onChange={(e) => setTo(e.target.value)} /></label>
          </>
        )}
        <div className="w-52"><WarehouseSelect includeAll value={wh} onChange={setWh} /></div>
        {(name === "stock" || name === "movements") && (
          <div className="w-44"><SimpleSelect allLabel="All categories" value={category} onChange={setCategory} options={(cats.data ?? []).map((c) => ({ label: c, value: c }))} /></div>
        )}
        {(name === "stock" || name === "movements" || name === "transfers") && (
          <Input className="w-48" placeholder="Item / SKU" value={q} onChange={(e) => setQ(e.target.value)} />
        )}
      </div>
      <QueryStates isLoading={query.isLoading} isError={query.isError} error={query.error} data={query.data} onRetry={() => query.refetch()}>
        {(r) => (
          <div className="space-y-2">
            <div>
              <h2 className="font-semibold">{r.title}</h2>
              <p className="text-muted-foreground text-sm">{r.subtitle}</p>
              {r.truncated && <p className="text-sm text-amber-600">Showing the first 5,000 rows; narrow the dates to see the rest.</p>}
            </div>
            {r.rows.length === 0 ? (
              <p className="text-muted-foreground bg-card rounded-md border py-10 text-center text-sm">No data for these filters.</p>
            ) : (
              <div className="bg-card max-h-[60vh] overflow-auto rounded-md border">
                <Table>
                  <TableHeader>
                    <TableRow>{r.columns.map((c) => <TableHead key={c}>{c}</TableHead>)}</TableRow>
                  </TableHeader>
                  <TableBody>
                    {r.rows.slice(0, 500).map((row, i) => (
                      <TableRow key={i}>
                        {row.map((cell, j) => (
                          <TableCell key={j} className={typeof cell === "number" ? "text-right tabular-nums" : ""}>
                            {typeof cell === "number" ? fmt(cell) : cell}
                          </TableCell>
                        ))}
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            )}
            {r.rows.length > 500 && <p className="text-muted-foreground text-xs">Preview shows 500 of {fmt(r.rows.length)} rows; the Excel export has all of them.</p>}
          </div>
        )}
      </QueryStates>
    </div>
  )
}

"use client"

import * as React from "react"
import Link from "next/link"
import {
  AlertTriangle,
  ArrowLeftRight,
  Activity,
  Barcode,
  Boxes,
  Layers,
  Package,
  PackageX,
  Tags,
  Warehouse,
} from "lucide-react"
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts"

import { fmt, SimpleSelect, StockBadge, WarehouseBadge, WarehouseSelect } from "@/components/ims/common"
import { PageHeader } from "@/components/shared/page-header"
import { QueryStates } from "@/components/shared/query-states"
import { StatTile } from "@/components/shared/stat-tile"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { formatDateTime } from "@/lib/format"
import { useDashboard } from "@/services/ims"
import { MOVEMENT_LABELS } from "@/types/ims"

function Section({ title, description, children }: { title: string; description?: string; children: React.ReactNode }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">{title}</CardTitle>
        {description && <CardDescription>{description}</CardDescription>}
      </CardHeader>
      <CardContent>{children}</CardContent>
    </Card>
  )
}

const Empty = ({ text }: { text: string }) => <p className="text-muted-foreground py-6 text-center text-sm">{text}</p>

export default function ImsDashboardPage() {
  const [warehouseId, setWarehouseId] = React.useState<string>()
  const [category, setCategory] = React.useState<string>()
  const [q, setQ] = React.useState("")
  const [dateFrom, setDateFrom] = React.useState("")
  const [dateTo, setDateTo] = React.useState("")
  const query = useDashboard({ warehouse_id: warehouseId, category, q, date_from: dateFrom, date_to: dateTo })

  return (
    <div className="space-y-6">
      <PageHeader title="Inventory Dashboard" description="Live stock position across warehouses, packaging and Shopify catalogue." />

      <div className="flex flex-wrap items-end gap-3">
        <div className="w-56">
          <WarehouseSelect includeAll value={warehouseId} onChange={setWarehouseId} />
        </div>
        <div className="w-48">
          <SimpleSelect
            allLabel="All categories"
            value={category}
            onChange={setCategory}
            options={(query.data?.categories ?? []).map((c) => ({ label: c, value: c }))}
          />
        </div>
        <Input className="w-52" placeholder="Product / SKU" value={q} onChange={(e) => setQ(e.target.value)} />
        <label className="text-muted-foreground text-xs">
          From
          <Input type="date" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} className="w-40" />
        </label>
        <label className="text-muted-foreground text-xs">
          To
          <Input type="date" value={dateTo} onChange={(e) => setDateTo(e.target.value)} className="w-40" />
        </label>
      </div>

      <QueryStates
        isLoading={query.isLoading}
        isError={query.isError}
        error={query.error}
        data={query.data}
        onRetry={() => query.refetch()}
      >
        {(d) => (
          <>
            {d.warnings.map((w) => (
              <Alert key={w}>
                <AlertDescription>{w}</AlertDescription>
              </Alert>
            ))}
            <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
              <StatTile label="Active Products" value={d.catalog ? fmt(d.catalog.active_products) : "—"} icon={Package} accent="blue" href="/ims/products" />
              <StatTile label="Product Categories" value={d.catalog ? fmt(d.catalog.categories) : "—"} icon={Tags} accent="violet" href="/ims/categories" />
              <StatTile
                label="Total SKUs"
                value={d.catalog ? fmt(d.catalog.total_skus) : "—"}
                icon={Barcode}
                accent="slate"
                subtext={d.catalog && d.catalog.missing_sku_variants > 0 ? `${d.catalog.missing_sku_variants} variants missing SKU` : undefined}
                href="/ims/skus"
              />
              <StatTile
                label="Total Physical Stock"
                value={fmt(d.cards.total_physical_stock)}
                icon={Boxes}
                accent="emerald"
                subtext={`${d.cards.tracked_finished_items} finished SKUs tracked`}
                href="/ims/stock"
              />
              <StatTile label="Total Warehouses" value={fmt(d.cards.total_warehouses)} icon={Warehouse} accent="blue" subtext={`${d.cards.active_warehouses} active`} href="/ims/warehouses" />
              <StatTile label="Low Stock Items" value={fmt(d.cards.low_stock_items)} icon={AlertTriangle} accent="amber" href="/ims/stock" />
              <StatTile label="Out of Stock Items" value={fmt(d.cards.out_of_stock_items)} icon={PackageX} accent="orange" href="/ims/stock" />
              <StatTile label="Recent Stock Movements" value={fmt(d.cards.recent_stock_movements)} icon={Activity} accent="violet" subtext={`${d.filters.date_from} → ${d.filters.date_to}`} href="/ims/history" />
            </div>

            {Object.keys(d.cards.packaging_stock_by_unit).length > 0 && (
              <div className="flex flex-wrap items-center gap-2 text-sm">
                <Layers className="text-muted-foreground size-4" />
                <span className="text-muted-foreground">Packaging on hand (never combined):</span>
                {Object.entries(d.cards.packaging_stock_by_unit).map(([u, n]) => (
                  <Badge key={u} variant="outline" className="tabular-nums">
                    {u}: {fmt(n)}
                  </Badge>
                ))}
              </div>
            )}

            <Section title="Warehouse overview" description="Per-location stock (finished goods and packaging kept apart).">
              <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
                {d.warehouses.map((w) => (
                  <Link key={w.id} href={`/ims/warehouses?focus=${w.id}`} className="block">
                    <div className={`hover:border-primary/40 h-full rounded-lg border p-4 transition-colors ${w.is_active ? "" : "opacity-60"}`}>
                      <div className="flex items-start justify-between">
                        <div>
                          <div className="font-semibold">{w.name}</div>
                          <WarehouseBadge code={w.code} />
                        </div>
                        {!w.is_active && <Badge variant="outline">Inactive</Badge>}
                      </div>
                      <dl className="mt-3 grid grid-cols-2 gap-y-1 text-sm">
                        <dt className="text-muted-foreground">Products</dt>
                        <dd className="text-right tabular-nums">{fmt(w.distinct_products)}</dd>
                        <dt className="text-muted-foreground">SKUs</dt>
                        <dd className="text-right tabular-nums">{fmt(w.finished_skus)}</dd>
                        <dt className="text-muted-foreground">Finished units</dt>
                        <dd className="text-right font-medium tabular-nums">{fmt(w.finished_quantity)}</dd>
                        <dt className="text-muted-foreground">Low stock</dt>
                        <dd className="text-right tabular-nums">{fmt(w.low_stock_count)}</dd>
                      </dl>
                      {Object.keys(w.packaging_quantity_by_unit).length > 0 && (
                        <div className="mt-2 flex flex-wrap gap-1">
                          {Object.entries(w.packaging_quantity_by_unit).map(([u, n]) => (
                            <Badge key={u} variant="secondary" className="text-[0.65rem] tabular-nums">
                              {u} {fmt(n)}
                            </Badge>
                          ))}
                        </div>
                      )}
                      <p className="text-muted-foreground mt-3 text-xs">
                        {w.last_activity ? `Last activity ${formatDateTime(w.last_activity)}` : "No activity yet"} ·{" "}
                        {w.movements_in_range} movements in range
                      </p>
                    </div>
                  </Link>
                ))}
              </div>
            </Section>

            <div className="grid gap-6 lg:grid-cols-2">
              <Section title="Stock distribution by warehouse" description="Finished-goods units.">
                {d.stock_distribution.every((s) => s.finished === 0) ? (
                  <Empty text="No finished-goods stock recorded yet." />
                ) : (
                  <div className="h-64">
                    <ResponsiveContainer width="100%" height="100%">
                      <BarChart data={d.stock_distribution}>
                        <CartesianGrid strokeDasharray="3 3" vertical={false} />
                        <XAxis dataKey="code" />
                        <YAxis />
                        <Tooltip />
                        <Bar dataKey="finished" name="Units" fill="var(--primary)" radius={[4, 4, 0, 0]} />
                      </BarChart>
                    </ResponsiveContainer>
                  </div>
                )}
              </Section>
              <Section title="Category-wise inventory" description="Finished-goods units by Shopify category.">
                {d.category_stock.every((c) => c.quantity === 0) ? (
                  <Empty text="No category stock yet." />
                ) : (
                  <div className="h-64">
                    <ResponsiveContainer width="100%" height="100%">
                      <BarChart data={d.category_stock} layout="vertical" margin={{ left: 40 }}>
                        <CartesianGrid strokeDasharray="3 3" horizontal={false} />
                        <XAxis type="number" />
                        <YAxis type="category" dataKey="category" width={110} />
                        <Tooltip />
                        <Bar dataKey="quantity" name="Units" fill="var(--primary)" radius={[0, 4, 4, 0]} />
                      </BarChart>
                    </ResponsiveContainer>
                  </div>
                )}
              </Section>
            </div>

            <div className="grid gap-6 lg:grid-cols-2">
              <Section title="Low-stock products">
                {d.low_stock.length === 0 ? <Empty text="Nothing is running low." /> : <StockList rows={d.low_stock} status="low_stock" />}
              </Section>
              <Section title="Out-of-stock products">
                {d.out_of_stock.length === 0 ? <Empty text="Nothing is out of stock." /> : <StockList rows={d.out_of_stock} status="out_of_stock" />}
              </Section>
            </div>

            <div className="grid gap-6 xl:grid-cols-3">
              <Section title="Recent transfers">
                {d.recent_transfers.length === 0 ? (
                  <Empty text="No transfers in range." />
                ) : (
                  d.recent_transfers.map((t) => (
                    <div key={t.id} className="flex items-center justify-between border-b py-2 text-sm last:border-0">
                      <div>
                        <div className="flex items-center gap-1 font-medium">
                          {t.source.code} <ArrowLeftRight className="size-3" /> {t.destination.code}
                        </div>
                        <div className="text-muted-foreground text-xs">{t.reference_no}</div>
                      </div>
                      <span className="tabular-nums">{fmt(t.total_quantity)}</span>
                    </div>
                  ))
                )}
              </Section>
              <Section title="Recent returns">
                {d.recent_returns.length === 0 ? (
                  <Empty text="No returns in range." />
                ) : (
                  d.recent_returns.map((r) => (
                    <div key={r.id} className="flex items-center justify-between border-b py-2 text-sm last:border-0">
                      <div>
                        <div className="font-medium">
                          {r.party?.name} → {r.warehouse.code}
                        </div>
                        <div className="text-muted-foreground text-xs">{r.reference_no}</div>
                      </div>
                      <span className="tabular-nums">{fmt(r.total_quantity)}</span>
                    </div>
                  ))
                )}
              </Section>
              <Section title="Recent adjustments">
                {d.recent_adjustments.length === 0 ? (
                  <Empty text="No adjustments in range." />
                ) : (
                  d.recent_adjustments.map((a) => (
                    <div key={a.id} className="flex items-center justify-between border-b py-2 text-sm last:border-0">
                      <div>
                        <div className="font-medium">{a.item.name}</div>
                        <div className="text-muted-foreground text-xs">
                          {MOVEMENT_LABELS[a.adjustment_type] ?? a.adjustment_type} · {a.warehouse.code} · {a.reason}
                        </div>
                      </div>
                      <span className={`tabular-nums ${a.direction === "out" ? "text-red-600" : "text-emerald-600"}`}>
                        {a.direction === "out" ? "−" : "+"}
                        {fmt(a.quantity)}
                      </span>
                    </div>
                  ))
                )}
              </Section>
            </div>
          </>
        )}
      </QueryStates>
    </div>
  )
}

function StockList({
  rows,
  status,
}: {
  rows: { id: string; name: string; sku: string | null; unit: string | null; total: number }[]
  status: "low_stock" | "out_of_stock"
}) {
  return (
    <div>
      {rows.map((r) => (
        <div key={r.id} className="flex items-center justify-between gap-2 border-b py-2 text-sm last:border-0">
          <div className="min-w-0">
            <div className="truncate font-medium">{r.name}</div>
            <div className="text-muted-foreground text-xs">{r.sku ?? r.unit ?? "—"}</div>
          </div>
          <div className="flex items-center gap-2">
            <span className="tabular-nums">{fmt(r.total)}</span>
            <StockBadge status={status} />
          </div>
        </div>
      ))}
    </div>
  )
}

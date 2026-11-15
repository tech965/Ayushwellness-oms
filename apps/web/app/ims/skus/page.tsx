"use client"

import * as React from "react"
import { useRouter } from "next/navigation"

import { fmt, Pager, SimpleSelect, useDebounced } from "@/components/ims/common"
import { DataTable, type DataTableColumn } from "@/components/shared/data-table"
import { PageHeader } from "@/components/shared/page-header"
import { ProductThumbnail } from "@/components/shared/product-thumbnail"
import { QueryStates } from "@/components/shared/query-states"
import { Badge } from "@/components/ui/badge"
import { Input } from "@/components/ui/input"
import { useCategoryOptions, useCatalogProducts } from "@/services/ims"
import type { CatalogProduct, CatalogVariant } from "@/types/ims"

type Row = CatalogVariant & { product: CatalogProduct }

export default function ImsSkusPage() {
  const router = useRouter()
  const [q, setQ] = React.useState("")
  const [category, setCategory] = React.useState<string>()
  const [missing, setMissing] = React.useState<string>()
  const [page, setPage] = React.useState(1)
  const dq = useDebounced(q)
  const query = useCatalogProducts({ q: dq, category, missing_sku: missing === "yes" ? true : undefined, page, page_size: 25 })
  const cats = useCategoryOptions()

  const columns: DataTableColumn<Row>[] = [
    {
      id: "sku",
      header: "SKU",
      cell: (r) => (r.sku_missing ? <Badge variant="destructive">Missing SKU</Badge> : <span className="font-mono text-xs">{r.sku}</span>),
    },
    {
      id: "product",
      header: "Product / variant",
      cell: (r) => (
        <div className="flex items-center gap-2">
          <ProductThumbnail src={r.image_url ?? r.product.image_url} alt={r.product.title} size="size-8" />
          <div>
            <div className="font-medium">{r.product.title}</div>
            <div className="text-muted-foreground text-xs">{r.title}</div>
          </div>
        </div>
      ),
    },
    { id: "barcode", header: "Barcode", cell: (r) => <span className="font-mono text-xs">{r.barcode ?? "—"}</span> },
    { id: "category", header: "Category", cell: (r) => r.product.category },
    { id: "status", header: "Status", cell: (r) => <Badge variant={r.product.status === "active" ? "default" : "outline"}>{r.product.status}</Badge> },
    { id: "stock", header: "Stock", className: "text-right", cell: (r) => <span className="tabular-nums">{fmt(r.total_stock)}</span> },
  ]

  return (
    <div className="space-y-4">
      <PageHeader title="SKUs" description="Every Shopify variant; inventory is tracked per variant." />
      <div className="flex flex-wrap gap-3">
        <Input className="w-64" placeholder="Search SKU, product, barcode…" value={q} onChange={(e) => { setQ(e.target.value); setPage(1) }} />
        <div className="w-48">
          <SimpleSelect allLabel="All categories" value={category} onChange={(v) => { setCategory(v); setPage(1) }} options={(cats.data ?? []).map((c) => ({ label: c, value: c }))} />
        </div>
        <div className="w-44">
          <SimpleSelect allLabel="All SKU states" value={missing} onChange={(v) => { setMissing(v); setPage(1) }} options={[{ label: "Missing SKU only", value: "yes" }]} />
        </div>
      </div>
      <QueryStates isLoading={query.isLoading} isError={query.isError} error={query.error} data={query.data} onRetry={() => query.refetch()} isEmpty={(d) => d.items.length === 0}>
        {(d) => {
          const rows: Row[] = d.items.flatMap((p) => p.variants.map((v) => ({ ...v, product: p })))
          return (
            <div className="space-y-3">
              <DataTable columns={columns} data={rows} rowKey={(r) => r.variant_id} onRowClick={(r) => router.push(`/ims/products/${r.product.product_id}`)} />
              <Pager page={page} pageSize={d.page_size} total={d.total_items} onPage={setPage} />
              <p className="text-muted-foreground text-xs">Paging is by product; a product with several variants shows all of them.</p>
            </div>
          )
        }}
      </QueryStates>
    </div>
  )
}

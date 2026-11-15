"use client"

import * as React from "react"
import { useRouter } from "next/navigation"
import { RefreshCw } from "lucide-react"
import { toast } from "sonner"

import { fmt, Pager, SimpleSelect, useDebounced } from "@/components/ims/common"
import { DataTable, type DataTableColumn } from "@/components/shared/data-table"
import { PageHeader } from "@/components/shared/page-header"
import { ProductThumbnail } from "@/components/shared/product-thumbnail"
import { QueryStates } from "@/components/shared/query-states"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { getApiErrorMessage } from "@/lib/api-client"
import { useAuth } from "@/lib/auth-context"
import { useCategoryOptions, useCatalogProducts, useRefreshCatalog } from "@/services/ims"
import type { CatalogProduct } from "@/types/ims"

export default function ImsProductsPage() {
  const router = useRouter()
  const { hasPermission } = useAuth()
  const [q, setQ] = React.useState("")
  const [category, setCategory] = React.useState<string | undefined>(() =>
    typeof window === "undefined" ? undefined : (new URLSearchParams(window.location.search).get("category") ?? undefined)
  )
  const [status, setStatus] = React.useState<string>("active")
  const [missing, setMissing] = React.useState<string>()
  const [page, setPage] = React.useState(1)
  const dq = useDebounced(q)
  const query = useCatalogProducts({
    q: dq,
    category,
    status: status === "all" ? undefined : status,
    missing_sku: missing === "yes" ? true : undefined,
    page,
    page_size: 25,
  })
  const cats = useCategoryOptions()
  const refresh = useRefreshCatalog()

  const columns: DataTableColumn<CatalogProduct>[] = [
    {
      id: "product",
      header: "Product",
      cell: (p) => (
        <div className="flex items-center gap-3">
          <ProductThumbnail src={p.image_url} alt={p.title} />
          <div>
            <div className="font-medium">{p.title}</div>
            <div className="text-muted-foreground text-xs">{p.vendor}</div>
          </div>
        </div>
      ),
    },
    { id: "category", header: "Category", cell: (p) => p.category },
    {
      id: "status",
      header: "Status",
      cell: (p) => <Badge variant={p.status === "active" ? "default" : "outline"}>{p.status}</Badge>,
    },
    {
      id: "variants",
      header: "SKUs",
      cell: (p) => (
        <span className="tabular-nums">
          {p.variants.length}
          {p.variants.some((v) => v.sku_missing) && (
            <Badge variant="destructive" className="ml-2">
              missing SKU
            </Badge>
          )}
        </span>
      ),
    },
    {
      id: "stock",
      header: "Physical stock",
      className: "text-right",
      cell: (p) => <span className="tabular-nums">{fmt(p.variants.reduce((a, v) => a + v.total_stock, 0))}</span>,
    },
  ]

  return (
    <div className="space-y-4">
      <PageHeader
        title="Products"
        description="Live from Shopify. Stock is maintained by the IMS and never overwritten by a catalogue refresh."
        actions={
          hasPermission("ims.manage") && (
            <Button
              variant="outline"
              disabled={refresh.isPending}
              onClick={() =>
                refresh.mutate(undefined, {
                  onSuccess: (r) => toast.success(`Refreshed ${r.products} products · ${r.labels_updated} labels updated`),
                  onError: (e) => toast.error(getApiErrorMessage(e)),
                })
              }
            >
              <RefreshCw className={`size-4 ${refresh.isPending ? "animate-spin" : ""}`} /> Refresh from Shopify
            </Button>
          )
        }
      />
      <div className="flex flex-wrap gap-3">
        <Input
          className="w-64"
          placeholder="Search product, SKU, barcode…"
          value={q}
          onChange={(e) => {
            setQ(e.target.value)
            setPage(1)
          }}
        />
        <div className="w-48">
          <SimpleSelect
            allLabel="All categories"
            value={category}
            onChange={(v) => {
              setCategory(v)
              setPage(1)
            }}
            options={(cats.data ?? []).map((c) => ({ label: c, value: c }))}
          />
        </div>
        <div className="w-40">
          <SimpleSelect
            value={status}
            onChange={(v) => {
              setStatus(v ?? "all")
              setPage(1)
            }}
            options={[
              { label: "Active", value: "active" },
              { label: "Draft", value: "draft" },
              { label: "Archived", value: "archived" },
              { label: "All statuses", value: "all" },
            ]}
          />
        </div>
        <div className="w-44">
          <SimpleSelect
            allLabel="All SKU states"
            value={missing}
            onChange={(v) => {
              setMissing(v)
              setPage(1)
            }}
            options={[{ label: "Missing SKU only", value: "yes" }]}
          />
        </div>
      </div>
      <QueryStates
        isLoading={query.isLoading}
        isError={query.isError}
        error={query.error}
        data={query.data}
        onRetry={() => query.refetch()}
        isEmpty={(d) => d.items.length === 0}
        emptyTitle="No products match"
      >
        {(d) => (
          <div className="space-y-3">
            {d.truncated && (
              <p className="text-sm text-amber-600">Catalogue is very large; only the first pages were loaded.</p>
            )}
            <DataTable columns={columns} data={d.items} rowKey={(p) => p.product_id} onRowClick={(p) => router.push(`/ims/products/${p.product_id}`)} />
            <Pager page={page} pageSize={d.page_size} total={d.total_items} onPage={setPage} />
          </div>
        )}
      </QueryStates>
    </div>
  )
}

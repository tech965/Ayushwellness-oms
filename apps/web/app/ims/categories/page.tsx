"use client"

import Link from "next/link"

import { fmt } from "@/components/ims/common"
import { DataTable, type DataTableColumn } from "@/components/shared/data-table"
import { PageHeader } from "@/components/shared/page-header"
import { QueryStates } from "@/components/shared/query-states"
import { useCatalogCategories } from "@/services/ims"

interface Row {
  category: string
  products: number
  active_products: number
  skus: number
}

const columns: DataTableColumn<Row>[] = [
  {
    id: "category",
    header: "Category (Shopify product type)",
    cell: (r) => (
      <Link className="text-primary font-medium hover:underline" href={`/ims/products?category=${encodeURIComponent(r.category)}`}>
        {r.category}
      </Link>
    ),
  },
  { id: "active", header: "Active products", className: "text-right", cell: (r) => <span className="tabular-nums">{fmt(r.active_products)}</span> },
  { id: "products", header: "All products", className: "text-right", cell: (r) => <span className="tabular-nums">{fmt(r.products)}</span> },
  { id: "skus", header: "SKUs", className: "text-right", cell: (r) => <span className="tabular-nums">{fmt(r.skus)}</span> },
]

export default function ImsCategoriesPage() {
  const query = useCatalogCategories()
  return (
    <div className="space-y-4">
      <PageHeader title="Categories" description="Categories are Shopify product types, read live." />
      <QueryStates isLoading={query.isLoading} isError={query.isError} error={query.error} data={query.data} onRetry={() => query.refetch()} isEmpty={(d) => d.length === 0}>
        {(rows) => <DataTable columns={columns} data={rows} rowKey={(r) => r.category} />}
      </QueryStates>
    </div>
  )
}

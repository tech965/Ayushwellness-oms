"use client"

import * as React from "react"

import { DataTable, type DataTableColumn } from "@/components/shared/data-table"
import { FilterBar } from "@/components/shared/filter-bar"
import { PageHeader } from "@/components/shared/page-header"
import { PaginationBar } from "@/components/shared/pagination-bar"
import { QueryStates } from "@/components/shared/query-states"
import { StatusBadge } from "@/components/shared/status-badge"
import { formatDate } from "@/lib/format"
import { usePaginationState } from "@/lib/use-pagination"
import { useProducts } from "@/services/products"
import { PRODUCT_STATUS_OPTIONS, type Product, type ProductStatus } from "@/types/product"

export default function ProductsPage() {
  const { page, pageSize, setPage, resetPage } = usePaginationState()
  const [search, setSearch] = React.useState("")
  const [status, setStatus] = React.useState<ProductStatus | undefined>(undefined)

  const query = useProducts({ page, pageSize, q: search, status })

  const columns: DataTableColumn<Product>[] = [
    {
      id: "title",
      header: "Title",
      cell: (product) => <span className="font-medium">{product.title}</span>,
    },
    { id: "vendor", header: "Vendor", cell: (product) => product.vendor ?? "—" },
    {
      id: "status",
      header: "Status",
      cell: (product) => <StatusBadge domain="product" status={product.status} />,
    },
    {
      id: "source",
      header: "Source",
      cell: (product) => product.source_system ?? "manual",
    },
    {
      id: "created",
      header: "Created",
      cell: (product) => formatDate(product.created_at),
    },
  ]

  return (
    <>
      <PageHeader
        title="Products"
        description="Products synced from Shopify starting Phase 2."
      />
      <div className="flex flex-col gap-4">
        <FilterBar
          searchValue={search}
          onSearchChange={(value) => {
            setSearch(value)
            resetPage()
          }}
          searchPlaceholder="Search by title or vendor..."
          statusValue={status}
          onStatusChange={(value) => {
            setStatus(value as ProductStatus | undefined)
            resetPage()
          }}
          statusOptions={PRODUCT_STATUS_OPTIONS}
          statusLabel="Status"
        />
        <QueryStates
          isLoading={query.isLoading}
          isError={query.isError}
          error={query.error}
          data={query.data}
          onRetry={() => void query.refetch()}
          isEmpty={(data) => data.data.length === 0}
          emptyTitle="No products found"
          emptyDescription="Try adjusting your search or filters."
        >
          {(data) => (
            <>
              <DataTable
                columns={columns}
                data={data.data}
                rowKey={(product) => product.id}
              />
              <PaginationBar meta={data.meta} onPageChange={setPage} />
            </>
          )}
        </QueryStates>
      </div>
    </>
  )
}

"use client"

import * as React from "react"
import { useRouter } from "next/navigation"

import { DataTable, type DataTableColumn } from "@/components/shared/data-table"
import { FilterBar } from "@/components/shared/filter-bar"
import { PageHeader } from "@/components/shared/page-header"
import { PaginationBar } from "@/components/shared/pagination-bar"
import { QueryStates } from "@/components/shared/query-states"
import { formatDate } from "@/lib/format"
import { usePaginationState } from "@/lib/use-pagination"
import { useCustomers } from "@/services/customers"
import type { Customer } from "@/types/customer"

export default function CustomersPage() {
  const router = useRouter()
  const { page, pageSize, setPage, resetPage } = usePaginationState()
  const [search, setSearch] = React.useState("")

  const query = useCustomers({ page, pageSize, q: search })

  const columns: DataTableColumn<Customer>[] = [
    {
      id: "name",
      header: "Name",
      cell: (customer) => (
        <span className="font-medium">{customer.full_name ?? "—"}</span>
      ),
    },
    { id: "email", header: "Email", cell: (customer) => customer.email ?? "—" },
    { id: "phone", header: "Phone", cell: (customer) => customer.phone ?? "—" },
    {
      id: "source",
      header: "Source",
      cell: (customer) => customer.source_system ?? "manual",
    },
    {
      id: "created",
      header: "Created",
      cell: (customer) => formatDate(customer.created_at),
    },
  ]

  return (
    <>
      <PageHeader title="Customers" description="Search and drill into any customer." />
      <div className="flex flex-col gap-4">
        <FilterBar
          searchValue={search}
          onSearchChange={(value) => {
            setSearch(value)
            resetPage()
          }}
          searchPlaceholder="Search by name, email, or phone..."
        />
        <QueryStates
          isLoading={query.isLoading}
          isError={query.isError}
          error={query.error}
          data={query.data}
          onRetry={() => void query.refetch()}
          isEmpty={(data) => data.data.length === 0}
          emptyTitle="No customers found"
          emptyDescription="Try adjusting your search."
        >
          {(data) => (
            <>
              <DataTable
                columns={columns}
                data={data.data}
                rowKey={(customer) => customer.id}
                onRowClick={(customer) => router.push(`/customers/${customer.id}`)}
              />
              <PaginationBar meta={data.meta} onPageChange={setPage} />
            </>
          )}
        </QueryStates>
      </div>
    </>
  )
}

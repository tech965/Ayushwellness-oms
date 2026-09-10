"use client"

import * as React from "react"
import { useRouter } from "next/navigation"

import { DataTable, type DataTableColumn } from "@/components/shared/data-table"
import { FilterBar } from "@/components/shared/filter-bar"
import { PageHeader } from "@/components/shared/page-header"
import { PaginationBar } from "@/components/shared/pagination-bar"
import { QueryStates } from "@/components/shared/query-states"
import { Badge } from "@/components/ui/badge"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { formatDate, formatMoney } from "@/lib/format"
import { usePaginationState } from "@/lib/use-pagination"
import { useCustomers, useRepeatCustomers } from "@/services/customers"
import type { Customer, RepeatCustomer } from "@/types/customer"

export default function CustomersPage() {
  const router = useRouter()
  const { page, pageSize, setPage, resetPage } = usePaginationState()
  const [search, setSearch] = React.useState("")
  const [view, setView] = React.useState<"all" | "repeat">("all")

  const query = useCustomers({ page, pageSize, q: search })
  const repeatQuery = useRepeatCustomers({ page, pageSize, q: search })

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

  const repeatColumns: DataTableColumn<RepeatCustomer>[] = [
    {
      id: "name",
      header: "Name",
      cell: (row) => <span className="font-medium">{row.customer_name ?? "—"}</span>,
    },
    { id: "phone", header: "Phone", cell: (row) => row.phone ?? "—" },
    {
      id: "order_count",
      header: "Orders",
      cell: (row) => <Badge variant="secondary">{row.order_count}</Badge>,
    },
    {
      id: "order_numbers",
      header: "Order Numbers",
      cell: (row) => (
        <span className="text-muted-foreground text-xs">{row.order_numbers.join(", ")}</span>
      ),
    },
    {
      id: "latest_order_at",
      header: "Latest Order",
      cell: (row) => (row.latest_order_at ? formatDate(row.latest_order_at) : "—"),
    },
    {
      id: "latest_status",
      header: "Status",
      cell: (row) => row.latest_order_status ?? "—",
    },
    {
      id: "latest_payment_status",
      header: "Payment",
      cell: (row) => row.latest_payment_status ?? "—",
    },
    {
      id: "total_value",
      header: "Total Value",
      cell: (row) => formatMoney(row.total_order_value),
      className: "text-right",
    },
  ]

  return (
    <>
      <PageHeader title="Customers" description="Search and drill into any customer." />
      <div className="flex flex-col gap-4">
        <Tabs
          value={view}
          onValueChange={(v) => {
            setView(v === "repeat" ? "repeat" : "all")
            resetPage()
          }}
        >
          <TabsList>
            <TabsTrigger value="all">All Customers</TabsTrigger>
            <TabsTrigger value="repeat">Repeat Customers</TabsTrigger>
          </TabsList>
        </Tabs>

        <FilterBar
          searchValue={search}
          onSearchChange={(value) => {
            setSearch(value)
            resetPage()
          }}
          searchPlaceholder="Search by name, email, or phone..."
        />

        {view === "all" ? (
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
        ) : (
          <QueryStates
            isLoading={repeatQuery.isLoading}
            isError={repeatQuery.isError}
            error={repeatQuery.error}
            data={repeatQuery.data}
            onRetry={() => void repeatQuery.refetch()}
            isEmpty={(data) => data.data.length === 0}
            emptyTitle="No repeat customers yet"
            emptyDescription="Customers with more than one order will show up here."
          >
            {(data) => (
              <>
                <DataTable
                  columns={repeatColumns}
                  data={data.data}
                  rowKey={(row) => row.customer_id}
                  onRowClick={(row) => router.push(`/customers/${row.customer_id}`)}
                />
                <PaginationBar meta={data.meta} onPageChange={setPage} />
              </>
            )}
          </QueryStates>
        )}
      </div>
    </>
  )
}

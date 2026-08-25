"use client"

import * as React from "react"
import { useRouter } from "next/navigation"

import { DataTable, type DataTableColumn } from "@/components/shared/data-table"
import { FilterBar } from "@/components/shared/filter-bar"
import { PageHeader } from "@/components/shared/page-header"
import { PaginationBar } from "@/components/shared/pagination-bar"
import { QueryStates } from "@/components/shared/query-states"
import { StatusBadge } from "@/components/shared/status-badge"
import { formatDate, formatMoney } from "@/lib/format"
import { usePaginationState } from "@/lib/use-pagination"
import { useOrders } from "@/services/orders"
import { ORDER_STATUS_OPTIONS, type Order, type OrderStatus } from "@/types/order"

export default function OrdersPage() {
  const router = useRouter()
  const { page, pageSize, setPage, resetPage } = usePaginationState()
  const [search, setSearch] = React.useState("")
  const [status, setStatus] = React.useState<OrderStatus | undefined>(undefined)

  const query = useOrders({ page, pageSize, q: search, status })

  const columns: DataTableColumn<Order>[] = [
    {
      id: "order_number",
      header: "Order",
      cell: (order) => <span className="font-medium">{order.order_number}</span>,
    },
    {
      id: "date",
      header: "Date",
      cell: (order) => formatDate(order.order_datetime),
    },
    {
      id: "amount",
      header: "Amount",
      cell: (order) => formatMoney(order.total_amount, order.currency),
    },
    {
      id: "payment",
      header: "Payment",
      cell: (order) => <StatusBadge domain="payment" status={order.payment_status} />,
    },
    {
      id: "status",
      header: "Status",
      cell: (order) => <StatusBadge domain="order" status={order.status} />,
    },
    {
      id: "created",
      header: "Created",
      cell: (order) => formatDate(order.created_at),
    },
  ]

  return (
    <>
      <PageHeader
        title="Orders"
        description="Search, filter, and drill into any order."
      />
      <div className="flex flex-col gap-4">
        <FilterBar
          searchValue={search}
          onSearchChange={(value) => {
            setSearch(value)
            resetPage()
          }}
          searchPlaceholder="Search by order number..."
          statusValue={status}
          onStatusChange={(value) => {
            setStatus(value as OrderStatus | undefined)
            resetPage()
          }}
          statusOptions={ORDER_STATUS_OPTIONS}
          statusLabel="Status"
        />
        <QueryStates
          isLoading={query.isLoading}
          isError={query.isError}
          error={query.error}
          data={query.data}
          onRetry={() => void query.refetch()}
          isEmpty={(data) => data.data.length === 0}
          emptyTitle="No orders found"
          emptyDescription="Try adjusting your search or filters."
        >
          {(data) => (
            <>
              <DataTable
                columns={columns}
                data={data.data}
                rowKey={(order) => order.id}
                onRowClick={(order) => router.push(`/orders/${order.id}`)}
              />
              <PaginationBar meta={data.meta} onPageChange={setPage} />
            </>
          )}
        </QueryStates>
      </div>
    </>
  )
}

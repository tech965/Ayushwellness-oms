"use client"

import type { ReactNode } from "react"
import { useParams, useRouter } from "next/navigation"

import { DataTable, type DataTableColumn } from "@/components/shared/data-table"
import { PageHeader } from "@/components/shared/page-header"
import { PaginationBar } from "@/components/shared/pagination-bar"
import { QueryStates } from "@/components/shared/query-states"
import { StatusBadge } from "@/components/shared/status-badge"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { formatDate, formatMoney } from "@/lib/format"
import { usePaginationState } from "@/lib/use-pagination"
import { useCustomer, useCustomerOrders, useCustomerSummary } from "@/services/customers"
import type { Order } from "@/types/order"

export default function CustomerDetailPage() {
  const params = useParams<{ id: string }>()
  const router = useRouter()
  const customerId = params.id

  const customerQuery = useCustomer(customerId)
  const summaryQuery = useCustomerSummary(customerId)
  const { page, pageSize, setPage } = usePaginationState()
  const ordersQuery = useCustomerOrders(customerId, page, pageSize)

  const orderColumns: DataTableColumn<Order>[] = [
    {
      id: "order_number",
      header: "Order",
      cell: (order) => <span className="font-medium">{order.order_number}</span>,
    },
    { id: "date", header: "Date", cell: (order) => formatDate(order.order_datetime) },
    {
      id: "amount",
      header: "Amount",
      cell: (order) => formatMoney(order.total_amount, order.currency),
    },
    {
      id: "status",
      header: "Status",
      cell: (order) => <StatusBadge domain="order" status={order.status} />,
    },
  ]

  return (
    <>
      <PageHeader
        title={customerQuery.data?.full_name ?? "Customer"}
        description={customerQuery.data?.email ?? undefined}
      />
      <QueryStates
        isLoading={customerQuery.isLoading}
        isError={customerQuery.isError}
        error={customerQuery.error}
        data={customerQuery.data}
        onRetry={() => void customerQuery.refetch()}
      >
        {(customer) => (
          <div className="flex flex-col gap-4">
            <Card>
              <CardHeader>
                <CardTitle>Customer 360</CardTitle>
              </CardHeader>
              <CardContent className="grid grid-cols-2 gap-4 sm:grid-cols-4">
                <Stat
                  label="Total orders"
                  value={summaryQuery.data?.total_orders ?? "—"}
                />
                <Stat
                  label="Total spent"
                  value={
                    summaryQuery.data ? formatMoney(summaryQuery.data.total_spent) : "—"
                  }
                />
                <Stat
                  label="Average order value"
                  value={
                    summaryQuery.data
                      ? formatMoney(summaryQuery.data.average_order_value)
                      : "—"
                  }
                />
                <Stat
                  label="Delivered"
                  value={summaryQuery.data?.delivered_orders ?? "—"}
                />
                <Stat
                  label="Cancelled"
                  value={summaryQuery.data?.cancelled_orders ?? "—"}
                />
                <Stat label="RTO" value={summaryQuery.data?.rto_orders ?? "—"} />
                <Stat label="Phone" value={customer.phone ?? "—"} />
                <Stat
                  label="Last order"
                  value={
                    summaryQuery.data?.last_order_at
                      ? formatDate(summaryQuery.data.last_order_at)
                      : "—"
                  }
                />
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>Recent orders</CardTitle>
              </CardHeader>
              <CardContent>
                <QueryStates
                  isLoading={ordersQuery.isLoading}
                  isError={ordersQuery.isError}
                  error={ordersQuery.error}
                  data={ordersQuery.data}
                  onRetry={() => void ordersQuery.refetch()}
                  isEmpty={(data) => data.data.length === 0}
                  emptyTitle="No orders yet"
                >
                  {(data) => (
                    <div className="flex flex-col gap-3">
                      <DataTable
                        columns={orderColumns}
                        data={data.data}
                        rowKey={(order) => order.id}
                        onRowClick={(order) => router.push(`/orders/${order.id}`)}
                      />
                      <PaginationBar meta={data.meta} onPageChange={setPage} />
                    </div>
                  )}
                </QueryStates>
              </CardContent>
            </Card>
          </div>
        )}
      </QueryStates>
    </>
  )
}

function Stat({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div>
      <p className="text-muted-foreground text-xs">{label}</p>
      <p className="text-base font-semibold">{value}</p>
    </div>
  )
}

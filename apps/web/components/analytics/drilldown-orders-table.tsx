"use client"

import Link from "next/link"
import { useRouter } from "next/navigation"
import { ArrowRight } from "lucide-react"

import { DataTable, type DataTableColumn } from "@/components/shared/data-table"
import { QueryStates } from "@/components/shared/query-states"
import { StatusBadge } from "@/components/shared/status-badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { formatDate, formatMoney } from "@/lib/format"
import { useOrders } from "@/services/orders"
import type { Order, OrderListFilters } from "@/types/order"

const PREVIEW_PAGE_SIZE = 10

const COLUMNS: DataTableColumn<Order>[] = [
  {
    id: "order_number",
    header: "Order ID",
    cell: (o) => <span className="font-medium">{o.order_number}</span>,
  },
  { id: "order_datetime", header: "Order Date", cell: (o) => formatDate(o.order_datetime) },
  { id: "customer_name", header: "Customer", cell: (o) => o.customer_name ?? "—" },
  {
    id: "total_amount",
    header: "Amount",
    className: "text-right",
    cell: (o) => formatMoney(o.total_amount, o.currency),
  },
  {
    id: "payment_status",
    header: "Payment Status",
    cell: (o) => <StatusBadge domain="payment" status={o.payment_status} />,
  },
  {
    id: "status",
    header: "Order Status",
    cell: (o) => <StatusBadge domain="order" status={o.status} />,
  },
  {
    id: "shipment_status",
    header: "Shipment",
    cell: (o) =>
      o.shipment_status ? <StatusBadge domain="shipment" status={o.shipment_status} /> : "—",
  },
]

interface DrilldownOrdersTableProps {
  title: string
  filters: OrderListFilters
  /** Same filters, encoded for the "View all in Orders" link -- full RBAC,
   * full columns, full filter toolbar all still apply there, this preview
   * table just avoids sending the user away to see any rows at all.
   */
  ordersHref: string
  /** Omits the Payment Status column -- for a drill-down whose filters
   * already pin the payment status (e.g. Pending COD's Fulfilled/
   * Unfulfilled breakdown), where repeating it on every row is redundant.
   * Defaults to shown, matching every existing caller.
   */
  hidePaymentColumn?: boolean
}

/** A compact, read-only preview of the orders matching the current
 * drill-down's filters -- reuses `useOrders` (the same hook, same
 * RBAC-gated `/orders` endpoint, same data) the full Orders page uses, so
 * there is no second, divergent order-fetching code path. Item 13's full
 * column set (phone/email/fulfillment/courier/etc.) stays one click away
 * via "View all", where `DataTable`'s column-visibility toggle already
 * exposes every one of them.
 */
export function DrilldownOrdersTable({
  title,
  filters,
  ordersHref,
  hidePaymentColumn = false,
}: DrilldownOrdersTableProps) {
  const router = useRouter()
  const query = useOrders({ page: 1, pageSize: PREVIEW_PAGE_SIZE, ...filters })
  const columns = hidePaymentColumn
    ? COLUMNS.filter((c) => c.id !== "payment_status")
    : COLUMNS

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between gap-2">
        <CardTitle>{title}</CardTitle>
        <Button asChild variant="outline" size="sm">
          <Link href={ordersHref}>
            View all
            <ArrowRight className="size-3.5" />
          </Link>
        </Button>
      </CardHeader>
      <CardContent>
        <QueryStates
          isLoading={query.isLoading}
          isError={query.isError}
          error={query.error}
          data={query.data}
          onRetry={() => void query.refetch()}
          isEmpty={(data) => data.data.length === 0}
          emptyTitle="No orders found"
          emptyDescription="No orders match this drill-down's filters yet."
        >
          {(data) => (
            <>
              <DataTable
                columns={columns}
                data={data.data}
                rowKey={(order) => order.id}
                onRowClick={(order) => router.push(`/orders/${order.id}`)}
              />
              {data.meta.total_items > PREVIEW_PAGE_SIZE && (
                <p className="text-muted-foreground mt-3 text-xs">
                  Showing {PREVIEW_PAGE_SIZE} of {data.meta.total_items.toLocaleString("en-IN")}{" "}
                  matching orders —{" "}
                  <Link href={ordersHref} className="text-primary hover:underline">
                    view all
                  </Link>
                  .
                </p>
              )}
            </>
          )}
        </QueryStates>
      </CardContent>
    </Card>
  )
}

"use client"

import * as React from "react"

import { DataTable, type DataTableColumn } from "@/components/shared/data-table"
import { PaginationBar } from "@/components/shared/pagination-bar"
import { QueryStates } from "@/components/shared/query-states"
import { StatusBadge } from "@/components/shared/status-badge"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { formatDate, formatMoney } from "@/lib/format"
import { useFulfillmentPaymentOrders } from "@/services/analytics"
import type { FulfillmentPaymentOrderRow } from "@/types/analytics"

const PAGE_SIZE = 10

const COLUMNS: DataTableColumn<FulfillmentPaymentOrderRow>[] = [
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
    cell: (o) => formatMoney(o.total_amount),
  },
  {
    id: "payment_type",
    header: "Payment",
    cell: (o) => <span className="capitalize">{o.payment_type}</span>,
  },
  {
    id: "unified_status",
    header: "Shipment Status",
    cell: (o) => <StatusBadge domain="shipment" status={o.unified_status} />,
  },
  { id: "courier_name", header: "Courier", cell: (o) => o.courier_name ?? "—" },
]

interface FulfillmentPaymentOrdersTableProps {
  title: string
  dateFrom: string
  dateTo: string
  unifiedStatus?: string
  fulfillmentStatus?: "fulfilled" | "unfulfilled"
  paymentType?: "cod" | "prepaid" | "other"
}

/** The order list behind any Fulfilled/Unfulfilled classification row or
 * any COD Orders dashboard KPI -- same `/analytics/fulfillment-payment-
 * orders` endpoint for both, so the rows shown here always match the
 * count/amount on the card that opened it.
 */
export function FulfillmentPaymentOrdersTable({
  title,
  dateFrom,
  dateTo,
  unifiedStatus,
  fulfillmentStatus,
  paymentType,
}: FulfillmentPaymentOrdersTableProps) {
  // No internal reset-on-filter-change effect: callers pass a `key` tied
  // to the selected filters (unified_status/fulfillment_status/
  // payment_type) so a new selection remounts this component fresh,
  // resetting `page` via plain `useState` initialization instead of an
  // effect-driven `setState` (avoids the cascading-render lint rule).
  const [page, setPage] = React.useState(1)

  const query = useFulfillmentPaymentOrders({
    date_from: dateFrom,
    date_to: dateTo,
    unifiedStatus,
    fulfillmentStatus,
    paymentType,
    page,
    pageSize: PAGE_SIZE,
  })

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">{title}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <QueryStates
          isLoading={query.isLoading}
          isError={query.isError}
          error={query.error}
          data={query.data}
          onRetry={() => query.refetch()}
          isEmpty={(d) => d.data.length === 0}
        >
          {(d) => (
            <>
              <DataTable
                columns={COLUMNS}
                data={d.data}
                rowKey={(o) => o.order_id}
              />
              <PaginationBar meta={d.meta} onPageChange={setPage} />
            </>
          )}
        </QueryStates>
      </CardContent>
    </Card>
  )
}

"use client"

import * as React from "react"
import Link from "next/link"
import { useParams } from "next/navigation"
import { toast } from "sonner"

import { PageHeader } from "@/components/shared/page-header"
import { QueryStates } from "@/components/shared/query-states"
import { StatusBadge } from "@/components/shared/status-badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { useAuth } from "@/lib/auth-context"
import { formatDateTime, formatMoney } from "@/lib/format"
import { getApiErrorMessage } from "@/lib/api-client"
import { usePaymentsForOrder } from "@/services/payments"
import { useRefundsForOrder } from "@/services/refunds"
import { useReturnsForOrder } from "@/services/returns"
import { useShipmentsForOrder } from "@/services/shipments"
import {
  useOrder,
  useOrderTimeline,
  useShipOrderViaShiprocket,
  useTransitionOrderStatus,
} from "@/services/orders"
import { useCustomer } from "@/services/customers"
import { ORDER_STATUS_OPTIONS, type OrderStatus } from "@/types/order"

export default function OrderDetailPage() {
  const params = useParams<{ id: string }>()
  const orderId = params.id
  const { hasPermission } = useAuth()

  const orderQuery = useOrder(orderId)
  const timelineQuery = useOrderTimeline(orderId)
  const paymentsQuery = usePaymentsForOrder(orderId)
  const shipmentsQuery = useShipmentsForOrder(orderId)
  const returnsQuery = useReturnsForOrder(orderId)
  const refundsQuery = useRefundsForOrder(orderId)
  const transition = useTransitionOrderStatus(orderId)
  const shipViaShiprocket = useShipOrderViaShiprocket(orderId)
  const customerId = orderQuery.data?.customer_id ?? undefined
  const customerQuery = useCustomer(customerId ?? "")

  const [nextStatus, setNextStatus] = React.useState<OrderStatus | undefined>(undefined)

  function handleTransition() {
    if (!nextStatus) return
    transition.mutate(
      { status: nextStatus },
      {
        onSuccess: () => {
          toast.success(`Order moved to ${nextStatus}.`)
          setNextStatus(undefined)
        },
        onError: (error) => toast.error(getApiErrorMessage(error)),
      }
    )
  }

  return (
    <>
      <PageHeader
        title={orderQuery.data ? `Order ${orderQuery.data.order_number}` : "Order"}
        description={`ID: ${orderId}`}
      />
      <QueryStates
        isLoading={orderQuery.isLoading}
        isError={orderQuery.isError}
        error={orderQuery.error}
        data={orderQuery.data}
        onRetry={() => void orderQuery.refetch()}
      >
        {(order) => (
          <div className="flex flex-col gap-4">
            <Card>
              <CardHeader className="flex flex-row flex-wrap items-center justify-between gap-3">
                <div className="flex items-center gap-2">
                  <StatusBadge domain="order" status={order.status} />
                  <StatusBadge domain="payment" status={order.payment_status} />
                </div>
                {(hasPermission("orders.update") || hasPermission("orders.cancel")) && (
                  <div className="flex items-center gap-2">
                    <Select
                      value={nextStatus ?? ""}
                      onValueChange={(value) => setNextStatus(value as OrderStatus)}
                    >
                      <SelectTrigger className="w-[180px]">
                        <SelectValue placeholder="Change status..." />
                      </SelectTrigger>
                      <SelectContent>
                        {ORDER_STATUS_OPTIONS.filter(
                          (option) => option.value !== order.status
                        ).map((option) => (
                          <SelectItem key={option.value} value={option.value}>
                            {option.label}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                    <Button
                      size="sm"
                      disabled={!nextStatus || transition.isPending}
                      onClick={handleTransition}
                    >
                      {transition.isPending ? "Updating..." : "Update"}
                    </Button>
                  </div>
                )}
              </CardHeader>
              <CardContent className="grid grid-cols-2 gap-4 sm:grid-cols-4">
                <SummaryStat
                  label="Subtotal"
                  value={formatMoney(order.subtotal, order.currency)}
                />
                <SummaryStat
                  label="Discount"
                  value={formatMoney(order.discount_amount, order.currency)}
                />
                <SummaryStat
                  label="Tax"
                  value={formatMoney(order.tax_amount, order.currency)}
                />
                <SummaryStat
                  label="Shipping"
                  value={formatMoney(order.shipping_charge, order.currency)}
                />
                <SummaryStat
                  label="Total"
                  value={formatMoney(order.total_amount, order.currency)}
                  emphasize
                />
                <SummaryStat
                  label="Payment type"
                  value={order.payment_type.toUpperCase()}
                />
                <SummaryStat
                  label="Order date"
                  value={formatDateTime(order.order_datetime)}
                />
                <SummaryStat
                  label="Customer"
                  value={
                    order.customer_id ? (
                      <Link
                        href={`/customers/${order.customer_id}`}
                        className="text-primary hover:underline"
                      >
                        {customerQuery.data?.full_name ?? "View customer"}
                      </Link>
                    ) : (
                      "—"
                    )
                  }
                />
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>Items</CardTitle>
              </CardHeader>
              <CardContent>
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>SKU</TableHead>
                      <TableHead>Product</TableHead>
                      <TableHead className="text-right">Qty</TableHead>
                      <TableHead className="text-right">Unit price</TableHead>
                      <TableHead className="text-right">Total</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {order.items.map((item) => (
                      <TableRow key={item.id}>
                        <TableCell className="font-mono text-xs">{item.sku}</TableCell>
                        <TableCell>{item.product_name}</TableCell>
                        <TableCell className="text-right">{item.quantity}</TableCell>
                        <TableCell className="text-right">
                          {formatMoney(item.unit_price, order.currency)}
                        </TableCell>
                        <TableCell className="text-right">
                          {formatMoney(item.total_amount, order.currency)}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </CardContent>
            </Card>

            <div className="grid gap-4 lg:grid-cols-2">
              <Card>
                <CardHeader>
                  <CardTitle>Payments</CardTitle>
                </CardHeader>
                <CardContent className="flex flex-col gap-2">
                  {paymentsQuery.data?.length ? (
                    paymentsQuery.data.map((payment) => (
                      <div
                        key={payment.id}
                        className="flex items-center justify-between text-sm"
                      >
                        <span>{formatMoney(payment.amount, payment.currency)}</span>
                        <StatusBadge domain="payment" status={payment.status} />
                      </div>
                    ))
                  ) : (
                    <p className="text-muted-foreground text-sm">No payments recorded.</p>
                  )}
                </CardContent>
              </Card>

              <Card>
                <CardHeader className="flex flex-row items-center justify-between gap-2">
                  <CardTitle>Shipments</CardTitle>
                  {hasPermission("shipments.update") && (
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={shipViaShiprocket.isPending}
                      onClick={() => {
                        shipViaShiprocket.mutate(undefined, {
                          onSuccess: () =>
                            toast.success("Shipment created via Shiprocket."),
                          onError: (error) => toast.error(getApiErrorMessage(error)),
                        })
                      }}
                    >
                      {shipViaShiprocket.isPending
                        ? "Shipping..."
                        : "Ship via Shiprocket"}
                    </Button>
                  )}
                </CardHeader>
                <CardContent className="flex flex-col gap-2">
                  {shipmentsQuery.data?.length ? (
                    shipmentsQuery.data.map((shipment) => (
                      <Link
                        key={shipment.id}
                        href={`/shipments/${shipment.id}`}
                        className="flex items-center justify-between text-sm hover:underline"
                      >
                        <span>{shipment.awb ?? "No AWB yet"}</span>
                        <StatusBadge domain="shipment" status={shipment.current_status} />
                      </Link>
                    ))
                  ) : (
                    <p className="text-muted-foreground text-sm">No shipments yet.</p>
                  )}
                </CardContent>
              </Card>

              <Card>
                <CardHeader>
                  <CardTitle>Returns</CardTitle>
                </CardHeader>
                <CardContent className="flex flex-col gap-2">
                  {returnsQuery.data?.length ? (
                    returnsQuery.data.map((ret) => (
                      <div
                        key={ret.id}
                        className="flex items-center justify-between text-sm"
                      >
                        <span>{ret.reason ?? "No reason given"}</span>
                        <StatusBadge domain="return" status={ret.status} />
                      </div>
                    ))
                  ) : (
                    <p className="text-muted-foreground text-sm">No returns.</p>
                  )}
                </CardContent>
              </Card>

              <Card>
                <CardHeader>
                  <CardTitle>Refunds</CardTitle>
                </CardHeader>
                <CardContent className="flex flex-col gap-2">
                  {refundsQuery.data?.length ? (
                    refundsQuery.data.map((refund) => (
                      <div
                        key={refund.id}
                        className="flex items-center justify-between text-sm"
                      >
                        <span>{formatMoney(refund.amount, order.currency)}</span>
                        <StatusBadge domain="refund" status={refund.status} />
                      </div>
                    ))
                  ) : (
                    <p className="text-muted-foreground text-sm">No refunds.</p>
                  )}
                </CardContent>
              </Card>
            </div>

            <Card>
              <CardHeader>
                <CardTitle>Timeline</CardTitle>
              </CardHeader>
              <CardContent>
                <QueryStates
                  isLoading={timelineQuery.isLoading}
                  isError={timelineQuery.isError}
                  error={timelineQuery.error}
                  data={timelineQuery.data}
                  onRetry={() => void timelineQuery.refetch()}
                  isEmpty={(events) => events.length === 0}
                  emptyTitle="No timeline events yet"
                >
                  {(events) => (
                    <ol className="flex flex-col gap-3">
                      {events.map((event) => (
                        <li
                          key={event.id}
                          className="border-border flex gap-3 border-l-2 pl-3"
                        >
                          <div className="flex flex-col">
                            <span className="text-sm font-medium">
                              {event.description ?? event.event_type}
                            </span>
                            <span className="text-muted-foreground text-xs">
                              {formatDateTime(event.created_at)} &middot; {event.source}
                            </span>
                          </div>
                        </li>
                      ))}
                    </ol>
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

function SummaryStat({
  label,
  value,
  emphasize,
}: {
  label: string
  value: React.ReactNode
  emphasize?: boolean
}) {
  return (
    <div>
      <p className="text-muted-foreground text-xs">{label}</p>
      <p className={emphasize ? "text-base font-semibold" : "text-sm font-medium"}>
        {value}
      </p>
    </div>
  )
}

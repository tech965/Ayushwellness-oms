"use client"

import * as React from "react"
import Link from "next/link"
import { useParams, useSearchParams } from "next/navigation"
import { ArrowLeft, Mail, MapPin, Phone, Truck } from "lucide-react"
import { toast } from "sonner"

import { Breadcrumbs } from "@/components/shared/breadcrumbs"
import { PageHeader } from "@/components/shared/page-header"
import { QueryStates } from "@/components/shared/query-states"
import { StatusBadge } from "@/components/shared/status-badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
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
  TableFooter,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { getApiErrorMessage } from "@/lib/api-client"
import { useAuth } from "@/lib/auth-context"
import { formatDateTime, formatMoney } from "@/lib/format"
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
import { ORDER_STATUS_OPTIONS, type OrderAddress, type OrderStatus } from "@/types/order"

function OrderDetailSkeleton() {
  return (
    <div className="flex flex-col gap-3">
      <Skeleton className="h-6 w-64" />
      <Skeleton className="h-32 w-full" />
      <Skeleton className="h-64 w-full" />
    </div>
  )
}

export default function OrderDetailPage() {
  return (
    <React.Suspense fallback={<OrderDetailSkeleton />}>
      <OrderDetailContent />
    </React.Suspense>
  )
}

function OrderDetailContent() {
  const params = useParams<{ id: string }>()
  const searchParams = useSearchParams()
  const orderId = params.id
  const { hasPermission } = useAuth()

  const from = searchParams.get("from")
  const ordersHref = from ? `/orders?${from}` : "/orders"

  const orderQuery = useOrder(orderId)
  const timelineQuery = useOrderTimeline(orderId)
  const paymentsQuery = usePaymentsForOrder(orderId)
  const shipmentsQuery = useShipmentsForOrder(orderId)
  const returnsQuery = useReturnsForOrder(orderId)
  const refundsQuery = useRefundsForOrder(orderId)
  const transition = useTransitionOrderStatus(orderId)
  const shipViaShiprocket = useShipOrderViaShiprocket(orderId)

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
      <Breadcrumbs
        items={[
          { label: "Orders", href: ordersHref },
          { label: orderQuery.data ? `Order ${orderQuery.data.order_number}` : "Order" },
        ]}
      />
      <PageHeader
        title={orderQuery.data ? `Order ${orderQuery.data.order_number}` : "Order"}
        description={`ID: ${orderId}`}
        actions={
          <Button variant="outline" size="sm" asChild>
            <Link href={ordersHref}>
              <ArrowLeft className="size-4" />
              Back to Orders
            </Link>
          </Button>
        }
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
                <SummaryStat label="Tax" value={formatMoney(order.tax_amount, order.currency)} />
                <SummaryStat
                  label="Shipping"
                  value={formatMoney(order.shipping_charge, order.currency)}
                />
                <SummaryStat
                  label="Total"
                  value={formatMoney(order.total_amount, order.currency)}
                  emphasize
                />
                <SummaryStat label="Payment type" value={order.payment_type.toUpperCase()} />
                <SummaryStat label="Order date" value={formatDateTime(order.order_datetime)} />
                <SummaryStat
                  label="Fulfillment"
                  value={<StatusBadge domain="fulfillment" status={order.fulfillment_status} />}
                />
              </CardContent>
            </Card>

            <div className="grid gap-4 lg:grid-cols-2">
              <Card>
                <CardHeader>
                  <CardTitle>Customer</CardTitle>
                </CardHeader>
                <CardContent className="flex flex-col gap-3">
                  {order.customer ? (
                    <>
                      <Link
                        href={`/customers/${order.customer.id}`}
                        className="text-primary text-sm font-medium hover:underline"
                      >
                        {order.customer.full_name ?? "View customer"}
                      </Link>
                      <div className="text-muted-foreground flex flex-col gap-1 text-sm">
                        {order.customer.phone && (
                          <span className="flex items-center gap-1.5">
                            <Phone className="size-3.5" />
                            {order.customer.phone}
                          </span>
                        )}
                        {order.customer.email && (
                          <span className="flex items-center gap-1.5">
                            <Mail className="size-3.5" />
                            {order.customer.email}
                          </span>
                        )}
                      </div>
                    </>
                  ) : order.customer_id ? (
                    <p className="text-muted-foreground text-sm">
                      Linked customer hasn&apos;t finished syncing yet.
                    </p>
                  ) : (
                    <p className="text-muted-foreground text-sm">
                      No customer linked to this order (guest checkout).
                    </p>
                  )}
                </CardContent>
              </Card>

              <Card>
                <CardHeader>
                  <CardTitle>Shipping Address</CardTitle>
                </CardHeader>
                <CardContent>
                  <AddressBlock address={order.shipping_address} />
                </CardContent>
              </Card>
            </div>

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
                      <TableHead className="text-right">Discount</TableHead>
                      <TableHead className="text-right">Tax</TableHead>
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
                          {formatMoney(item.discount_amount, order.currency)}
                        </TableCell>
                        <TableCell className="text-right">
                          {formatMoney(item.tax_amount, order.currency)}
                        </TableCell>
                        <TableCell className="text-right font-medium">
                          {formatMoney(item.total_amount, order.currency)}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                  <TableFooter>
                    <TableRow>
                      <TableCell colSpan={6} className="text-right font-medium">
                        Order total
                      </TableCell>
                      <TableCell className="text-right font-semibold">
                        {formatMoney(order.total_amount, order.currency)}
                      </TableCell>
                    </TableRow>
                  </TableFooter>
                </Table>
              </CardContent>
            </Card>

            <div className="grid gap-4 lg:grid-cols-2">
              <Card>
                <CardHeader>
                  <CardTitle>Payments</CardTitle>
                </CardHeader>
                <CardContent className="flex flex-col gap-3">
                  {paymentsQuery.data?.length ? (
                    paymentsQuery.data.map((payment) => (
                      <div key={payment.id} className="flex flex-col gap-1 border-b pb-2 last:border-0 last:pb-0">
                        <div className="flex items-center justify-between text-sm">
                          <span className="font-medium">
                            {formatMoney(payment.amount, payment.currency)}
                          </span>
                          <StatusBadge domain="payment" status={payment.status} />
                        </div>
                        <div className="text-muted-foreground flex flex-wrap gap-x-3 text-xs">
                          {payment.provider && <span>{payment.provider}</span>}
                          {payment.external_transaction_id && (
                            <span className="font-mono">{payment.external_transaction_id}</span>
                          )}
                          {payment.paid_at && <span>Paid {formatDateTime(payment.paid_at)}</span>}
                        </div>
                      </div>
                    ))
                  ) : (
                    <p className="text-muted-foreground text-sm">No payments recorded.</p>
                  )}
                </CardContent>
              </Card>

              <Card>
                <CardHeader className="flex flex-row items-center justify-between gap-2">
                  <CardTitle>Shipment</CardTitle>
                  {hasPermission("shipments.update") && (
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={shipViaShiprocket.isPending}
                      onClick={() => {
                        shipViaShiprocket.mutate(undefined, {
                          onSuccess: () => toast.success("Shipment created via Shiprocket."),
                          onError: (error) => toast.error(getApiErrorMessage(error)),
                        })
                      }}
                    >
                      {shipViaShiprocket.isPending ? "Shipping..." : "Ship via Shiprocket"}
                    </Button>
                  )}
                </CardHeader>
                <CardContent className="flex flex-col gap-3">
                  {shipmentsQuery.data?.length ? (
                    shipmentsQuery.data.map((shipment) => (
                      <div key={shipment.id} className="flex flex-col gap-1.5 border-b pb-3 last:border-0 last:pb-0">
                        <div className="flex items-center justify-between">
                          <span className="font-mono text-sm">{shipment.awb ?? "No AWB yet"}</span>
                          <StatusBadge domain="shipment" status={shipment.current_status} />
                        </div>
                        <div className="text-muted-foreground flex flex-wrap gap-x-3 text-xs">
                          {shipment.expected_delivery_date && (
                            <span>
                              Expected {formatDateTime(shipment.expected_delivery_date)}
                            </span>
                          )}
                          {shipment.current_location && (
                            <span className="flex items-center gap-1">
                              <MapPin className="size-3" />
                              {shipment.current_location}
                            </span>
                          )}
                        </div>
                        <Link
                          href={`/shipments/${shipment.id}`}
                          className="text-primary flex w-fit items-center gap-1 text-xs hover:underline"
                        >
                          <Truck className="size-3.5" />
                          Track shipment
                        </Link>
                      </div>
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
                      <div key={ret.id} className="flex items-center justify-between text-sm">
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
                      <div key={refund.id} className="flex items-center justify-between text-sm">
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
                        <li key={event.id} className="border-border flex gap-3 border-l-2 pl-3">
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
      <p className={emphasize ? "text-base font-semibold" : "text-sm font-medium"}>{value}</p>
    </div>
  )
}

function AddressBlock({ address }: { address: OrderAddress | null }) {
  if (!address) {
    return <p className="text-muted-foreground text-sm">No address on file.</p>
  }
  return (
    <div className="text-sm">
      {address.contact_name && <p className="font-medium">{address.contact_name}</p>}
      <p className="text-muted-foreground">
        {address.line1}
        {address.line2 ? `, ${address.line2}` : ""}
      </p>
      <p className="text-muted-foreground">
        {address.city}
        {address.state ? `, ${address.state}` : ""} {address.pin_code}
      </p>
      <p className="text-muted-foreground">{address.country}</p>
      {address.contact_phone && (
        <p className="text-muted-foreground mt-1 flex items-center gap-1.5">
          <Phone className="size-3.5" />
          {address.contact_phone}
        </p>
      )}
    </div>
  )
}

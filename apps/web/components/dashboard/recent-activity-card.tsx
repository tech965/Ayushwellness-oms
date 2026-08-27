import type { ReactNode } from "react"
import Link from "next/link"

import { StatusBadge } from "@/components/shared/status-badge"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { formatMoney } from "@/lib/format"
import type { RecentActivity } from "@/types/analytics"

function ViewAll({ href }: { href: string }) {
  return (
    <Link href={href} className="text-primary text-xs font-medium hover:underline">
      View all
    </Link>
  )
}

function Row({ children }: { children: ReactNode }) {
  return <div className="flex items-center justify-between gap-2 text-sm">{children}</div>
}

export function RecentActivityCard({
  data,
  isLoading,
}: {
  data: RecentActivity | undefined
  isLoading: boolean
}) {
  if (isLoading || !data) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Recent Activity</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="bg-muted h-64 w-full animate-pulse rounded-md" />
        </CardContent>
      </Card>
    )
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Recent Activity</CardTitle>
      </CardHeader>
      <CardContent className="grid gap-6 sm:grid-cols-2">
        <div className="flex flex-col gap-2">
          <div className="flex items-center justify-between">
            <h4 className="text-muted-foreground text-xs font-semibold uppercase">
              Recent Orders
            </h4>
            <ViewAll href="/orders" />
          </div>
          {data.recent_orders.length === 0 && <EmptyRow />}
          {data.recent_orders.map((order) => (
            <Link
              key={order.id}
              href={`/orders/${order.id}`}
              className="hover:bg-accent -mx-2 rounded-md px-2 py-1"
            >
              <Row>
                <span className="font-medium">{order.order_number}</span>
                <span className="text-muted-foreground">
                  {formatMoney(order.total_amount)}
                </span>
              </Row>
            </Link>
          ))}
        </div>

        <div className="flex flex-col gap-2">
          <div className="flex items-center justify-between">
            <h4 className="text-muted-foreground text-xs font-semibold uppercase">
              Recent Shipments
            </h4>
            <ViewAll href="/shipments" />
          </div>
          {data.recent_shipments.length === 0 && <EmptyRow />}
          {data.recent_shipments.map((shipment) => (
            <Link
              key={shipment.id}
              href={`/shipments/${shipment.id}`}
              className="hover:bg-accent -mx-2 rounded-md px-2 py-1"
            >
              <Row>
                <span className="font-mono text-xs">{shipment.awb ?? "No AWB"}</span>
                <StatusBadge domain="shipment" status={shipment.current_status} />
              </Row>
            </Link>
          ))}
        </div>

        <div className="flex flex-col gap-2">
          <div className="flex items-center justify-between">
            <h4 className="text-muted-foreground text-xs font-semibold uppercase">
              Recent NDR / RTO
            </h4>
            <ViewAll href="/ndr" />
          </div>
          {data.recent_ndr_rto.length === 0 && <EmptyRow />}
          {data.recent_ndr_rto.map((event) => (
            <Link
              key={`${event.kind}-${event.id}`}
              href={`/orders/${event.order_id}`}
              className="hover:bg-accent -mx-2 rounded-md px-2 py-1"
            >
              <Row>
                <span className="uppercase">{event.kind}</span>
                <StatusBadge domain={event.kind} status={event.status} />
              </Row>
            </Link>
          ))}
        </div>

        <div className="flex flex-col gap-2">
          <div className="flex items-center justify-between">
            <h4 className="text-muted-foreground text-xs font-semibold uppercase">
              Recent Payments
            </h4>
            <ViewAll href="/orders" />
          </div>
          {data.recent_payments.length === 0 && <EmptyRow />}
          {data.recent_payments.map((payment) => (
            <Link
              key={payment.id}
              href={`/orders/${payment.order_id}`}
              className="hover:bg-accent -mx-2 rounded-md px-2 py-1"
            >
              <Row>
                <span className="font-medium">{formatMoney(payment.amount)}</span>
                <StatusBadge domain="payment" status={payment.status} />
              </Row>
            </Link>
          ))}
        </div>
      </CardContent>
    </Card>
  )
}

function EmptyRow() {
  return <p className="text-muted-foreground text-sm">Nothing yet.</p>
}

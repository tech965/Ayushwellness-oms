import Link from "next/link"

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { formatMoney } from "@/lib/format"
import { PriorityBadge } from "./priority-badge"
import type { AttentionItem } from "@/types/operations-command-center"

const ICON_BY_TYPE: Record<string, string> = {
  unfulfilled_orders: "🔴",
  pending_payments: "🟠",
  shipment_pending: "🟡",
  ndr_risk: "🔴",
  rto_risk: "🔴",
  pending_returns: "🟣",
  pending_refunds: "🟣",
  cod_pending_fulfillment: "🔴",
}

/** "Requires Attention" (spec section 5) -- every count/amount here is
 * `AttentionItem` straight from the API; the href points at the
 * existing Orders/NDR/RTO/Returns/Refunds pages' own already-supported
 * filter params (see `OperationsCommandCenterService._build_attention_items`),
 * never a new page or a param those pages don't already read.
 */
export function AttentionSection({ items }: { items: AttentionItem[] }) {
  const nonZero = items.filter((item) => item.count > 0)

  return (
    <Card>
      <CardHeader>
        <CardTitle>Requires Attention</CardTitle>
      </CardHeader>
      <CardContent>
        {nonZero.length === 0 ? (
          <p className="text-muted-foreground text-sm">
            Nothing requires attention in this period.
          </p>
        ) : (
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            {nonZero.map((item) => (
              <Link
                key={item.type}
                href={item.href}
                className="hover:border-primary/40 flex flex-col gap-2 rounded-lg border p-4 transition-colors"
              >
                <div className="flex items-center justify-between">
                  <span className="text-lg">{ICON_BY_TYPE[item.type] ?? "•"}</span>
                  <PriorityBadge value={item.priority} />
                </div>
                <p className="text-2xl font-semibold tabular-nums">
                  {item.count.toLocaleString("en-IN")}
                </p>
                <p className="text-muted-foreground text-xs">{item.label}</p>
                {item.amount ? (
                  <p className="text-muted-foreground text-xs">{formatMoney(item.amount)}</p>
                ) : null}
              </Link>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  )
}

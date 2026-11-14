"use client"

import { Badge } from "@/components/ui/badge"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { getApiErrorMessage } from "@/lib/api-client"
import { formatMoney } from "@/lib/format"
import { useAmazonOrdersByDate } from "@/services/amazon-sales"

function PaymentBadge({ method }: { method: "COD" | "Prepaid" | "Unknown" }) {
  const variant = method === "COD" ? "secondary" : method === "Prepaid" ? "default" : "outline"
  return <Badge variant={variant}>{method}</Badge>
}

/** Order-level drill-down for one day: product + COD/Prepaid payment
 * method only. Deliberately shows no buyer name, email, phone or
 * address -- the backend never fetches those fields in the first place. */
export function AmazonOrderDrilldown({
  date,
  onOpenChange,
}: {
  date: string | null
  onOpenChange: (open: boolean) => void
}) {
  const query = useAmazonOrdersByDate(date)

  return (
    <Dialog open={!!date} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[80vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>Orders on {date}</DialogTitle>
          <DialogDescription>
            Product and payment method (COD/Prepaid) only, live from Amazon -- no buyer name,
            email, phone or address is fetched or shown.
          </DialogDescription>
        </DialogHeader>

        {query.isLoading && (
          <div className="space-y-2">
            <Skeleton className="h-8 w-full" />
            <Skeleton className="h-8 w-full" />
            <Skeleton className="h-8 w-full" />
          </div>
        )}

        {query.isError && (
          <p className="text-destructive text-sm">{getApiErrorMessage(query.error)}</p>
        )}

        {query.data && (
          <div className="space-y-4">
            {query.data.truncated && query.data.truncated_note && (
              <p className="text-muted-foreground text-xs">{query.data.truncated_note}</p>
            )}
            {query.data.orders.length === 0 && (
              <p className="text-muted-foreground text-sm">No orders on this date.</p>
            )}
            {query.data.orders.map((o) => (
              <div key={o.order_id} className="rounded-md border p-3">
                <div className="mb-2 flex flex-wrap items-center gap-2 text-sm">
                  <span className="font-medium">{o.order_id}</span>
                  <Badge variant="outline">{o.order_status}</Badge>
                  <PaymentBadge method={o.payment_method} />
                </div>
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Product</TableHead>
                      <TableHead>SKU</TableHead>
                      <TableHead className="text-right">Qty</TableHead>
                      <TableHead className="text-right">Item price</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {o.items.map((it, i) => (
                      <TableRow key={`${it.asin}-${i}`}>
                        <TableCell className="max-w-xs truncate">
                          {it.product_name ?? it.asin}
                        </TableCell>
                        <TableCell>{it.sku ?? "—"}</TableCell>
                        <TableCell className="text-right">{it.units_ordered}</TableCell>
                        <TableCell className="text-right">
                          {it.item_price == null ? "Unavailable" : formatMoney(it.item_price, "INR")}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            ))}
          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}

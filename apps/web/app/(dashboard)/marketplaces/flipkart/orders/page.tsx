"use client"

import * as React from "react"
import { Download } from "lucide-react"
import { toast } from "sonner"

import { PageHeader } from "@/components/shared/page-header"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
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
import { formatDateTime, formatMoney } from "@/lib/format"
import {
  downloadFlipkartOrdersExport,
  useFlipkartOrders,
  useFlipkartStatus,
} from "@/services/flipkart-sales"

const STATUS_OPTIONS = [
  { value: "__all__", label: "All (pre-dispatch)" },
  { value: "APPROVED", label: "Approved" },
  { value: "PACKING_IN_PROGRESS", label: "Packing in progress" },
  { value: "PACKED", label: "Packed" },
  { value: "READY_TO_DISPATCH", label: "Ready to dispatch" },
  { value: "SHIPPED", label: "Shipped" },
  { value: "DELIVERED", label: "Delivered" },
  { value: "PICKUP_COMPLETE", label: "Pickup complete" },
  { value: "CANCELLED", label: "Cancelled" },
]

const UNAVAILABLE = "Unavailable"

function isoDaysAgo(n: number): string {
  const d = new Date(Date.now() - n * 86400000)
  return d.toISOString().slice(0, 10)
}

export default function FlipkartOrdersPage() {
  const status = useFlipkartStatus()
  const [from, setFrom] = React.useState(isoDaysAgo(7))
  const [to, setTo] = React.useState(isoDaysAgo(0))
  const [statusFilter, setStatusFilter] = React.useState("__all__")
  const [cursor, setCursor] = React.useState<string | undefined>(undefined)
  const [cursorStack, setCursorStack] = React.useState<(string | undefined)[]>([])
  const [exporting, setExporting] = React.useState(false)

  const st = status.data
  const params = {
    date_from: from,
    date_to: to,
    status: statusFilter === "__all__" ? undefined : statusFilter,
    page_size: 20,
    next_page_url: cursor,
  }
  const orders = useFlipkartOrders(params, !!st?.configured)

  const resetPaging = () => {
    setCursor(undefined)
    setCursorStack([])
  }

  const goNext = () => {
    if (!orders.data?.next_page_url) return
    setCursorStack((s) => [...s, cursor])
    setCursor(orders.data.next_page_url)
  }

  const goPrev = () => {
    setCursorStack((s) => {
      const next = [...s]
      const prev = next.pop()
      setCursor(prev)
      return next
    })
  }

  const handleExport = async () => {
    setExporting(true)
    try {
      await downloadFlipkartOrdersExport({
        date_from: from,
        date_to: to,
        status: statusFilter === "__all__" ? undefined : statusFilter,
      })
    } catch (e) {
      toast.error(getApiErrorMessage(e))
    } finally {
      setExporting(false)
    }
  }

  return (
    <>
      <PageHeader
        title="Flipkart · Orders"
        description="Read-only orders from the Flipkart Seller API, fetched live. Nothing is stored in the OMS."
        actions={
          <Button
            variant="outline"
            disabled={!st?.configured || exporting}
            onClick={handleExport}
          >
            <Download className="mr-2 size-4" /> {exporting ? "Exporting…" : "Export Excel"}
          </Button>
        }
      />

      {st && !st.configured && (
        <Alert className="mb-4" variant="destructive">
          <AlertTitle>Flipkart is not configured</AlertTitle>
          <AlertDescription>
            Missing environment variables: {st.missing.join(", ")}. No data is shown until real
            credentials are configured -- demo orders are never displayed.
          </AlertDescription>
        </Alert>
      )}

      <div className="mb-4 flex flex-wrap items-end gap-3">
        <label className="text-sm">
          From
          <Input
            type="date"
            value={from}
            max={to}
            onChange={(e) => {
              setFrom(e.target.value)
              resetPaging()
            }}
          />
        </label>
        <label className="text-sm">
          To
          <Input
            type="date"
            value={to}
            min={from}
            onChange={(e) => {
              setTo(e.target.value)
              resetPaging()
            }}
          />
        </label>
        <label className="text-sm">
          Status
          <Select
            value={statusFilter}
            onValueChange={(v) => {
              setStatusFilter(v)
              resetPaging()
            }}
          >
            <SelectTrigger className="w-[200px]">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {STATUS_OPTIONS.map((o) => (
                <SelectItem key={o.value} value={o.value}>
                  {o.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </label>
        <span className="text-muted-foreground text-xs">
          Last successful fetch:{" "}
          {st?.last_successful_fetch_at ? formatDateTime(st.last_successful_fetch_at) : "never"}
        </span>
      </div>

      {orders.isLoading && <Skeleton className="h-40 w-full" />}
      {orders.isError && (
        <Alert className="mb-4" variant="destructive">
          <AlertTitle>Flipkart request failed</AlertTitle>
          <AlertDescription>{getApiErrorMessage(orders.error)}</AlertDescription>
        </Alert>
      )}

      {orders.data && (
        <div className="space-y-4">
          {orders.data.warnings.map((w) => (
            <Alert key={w}>
              <AlertTitle>Note</AlertTitle>
              <AlertDescription>{w}</AlertDescription>
            </Alert>
          ))}

          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Order ID</TableHead>
                <TableHead>Order date</TableHead>
                <TableHead>Product</TableHead>
                <TableHead>SKU</TableHead>
                <TableHead>FSN</TableHead>
                <TableHead className="text-right">Qty</TableHead>
                <TableHead className="text-right">Selling price</TableHead>
                <TableHead>Payment type</TableHead>
                <TableHead>Status</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {orders.data.items.length === 0 && (
                <TableRow>
                  <TableCell colSpan={9} className="text-muted-foreground text-center">
                    No orders found.
                  </TableCell>
                </TableRow>
              )}
              {orders.data.items.map((it, i) => (
                <TableRow key={`${it.order_id}-${i}`}>
                  <TableCell>{it.order_id}</TableCell>
                  <TableCell>{it.order_date}</TableCell>
                  <TableCell className="max-w-xs truncate">
                    {it.product_name ?? UNAVAILABLE}
                  </TableCell>
                  <TableCell>{it.sku ?? UNAVAILABLE}</TableCell>
                  <TableCell>{it.fsn ?? UNAVAILABLE}</TableCell>
                  <TableCell className="text-right">{it.quantity}</TableCell>
                  <TableCell className="text-right">
                    {it.selling_price == null ? UNAVAILABLE : formatMoney(it.selling_price, "INR")}
                  </TableCell>
                  <TableCell>
                    <Badge variant="outline">{UNAVAILABLE}</Badge>
                  </TableCell>
                  <TableCell>{it.status}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>

          <div className="flex items-center gap-2">
            <Button variant="outline" size="sm" disabled={cursorStack.length === 0} onClick={goPrev}>
              Previous page
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={!orders.data.has_more || !orders.data.next_page_url}
              onClick={goNext}
            >
              Next page
            </Button>
            <span className="text-muted-foreground text-xs">
              {orders.data.has_more ? "More orders available" : "Last page"}
            </span>
          </div>

          <div className="text-muted-foreground space-y-1 text-xs">
            {Object.entries(orders.data.unavailable).map(([k, v]) => (
              <p key={k}>
                <strong>Unavailable -- {k}:</strong> {v}
              </p>
            ))}
          </div>
        </div>
      )}
    </>
  )
}

"use client"

import * as React from "react"
import { useRouter } from "next/navigation"
import { Download, PackageCheck, PackageX, RefreshCw } from "lucide-react"
import { toast } from "sonner"

import { DataTable, type DataTableColumn } from "@/components/shared/data-table"
import { StatTile } from "@/components/shared/stat-tile"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { formatDate, formatMoney } from "@/lib/format"
import {
  useExportRevenueOrders,
  useRevenueOrders,
  useRevenueSummary,
  useSyncCodRemittances,
  type RevenueOrderRow,
} from "@/services/revenue-reconciliation"

const PAGE_SIZE = 20

interface Props {
  paymentType: "cod" | "prepaid"
  label: string
  dateFrom: string
  dateTo: string
}

function money(v: string | null, currency = "INR") {
  return v === null ? "—" : formatMoney(v, currency)
}

/** Fulfilled/Unfulfilled split for one payment type, with the matching
 * Shopify orders (customer, amount, payment method, fulfillment, tracking)
 * and -- for COD -- Shiprocket collection/remittance figures. Figures from
 * Shiprocket are "reported"; "Bank verified" is a separate, manual check.
 */
export function CodReconciliationSection({ paymentType, label, dateFrom, dateTo }: Props) {
  const router = useRouter()
  const isCod = paymentType === "cod"
  const [fulfillment, setFulfillment] = React.useState<"fulfilled" | "unfulfilled" | null>(null)
  const [search, setSearch] = React.useState("")
  const [query, setQuery] = React.useState("")
  const [from, setFrom] = React.useState(dateFrom.slice(0, 10))
  const [to, setTo] = React.useState(dateTo.slice(0, 10))
  const [page, setPage] = React.useState(1)

  React.useEffect(() => {
    const t = setTimeout(() => {
      setQuery(search.trim())
      setPage(1)
    }, 300)
    return () => clearTimeout(t)
  }, [search])

  const dateParams = {
    date_from: from ? new Date(`${from}T00:00:00`).toISOString() : undefined,
    date_to: to ? new Date(`${to}T23:59:59`).toISOString() : undefined,
  }
  const summaryQuery = useRevenueSummary(dateParams)
  const filters = {
    payment_type: paymentType,
    fulfillment: fulfillment ?? undefined,
    q: query,
    ...dateParams,
  }
  const ordersQuery = useRevenueOrders({ ...filters, page, pageSize: PAGE_SIZE }, fulfillment !== null)
  const exportMutation = useExportRevenueOrders()
  const syncMutation = useSyncCodRemittances()

  const split = summaryQuery.data?.[paymentType]
  const fin = summaryQuery.data?.cod_financials

  const columns: DataTableColumn<RevenueOrderRow>[] = [
    { id: "order", header: "Order", cell: (o) => <span className="font-medium">{o.order_number}</span> },
    { id: "date", header: "Date", cell: (o) => formatDate(o.order_datetime) },
    {
      id: "customer",
      header: "Customer",
      cell: (o) => (
        <div>
          <div>{o.customer_name ?? "—"}</div>
          <div className="text-muted-foreground text-xs">{o.customer_phone ?? ""}</div>
        </div>
      ),
    },
    { id: "amount", header: "Amount", className: "text-right", cell: (o) => formatMoney(o.amount, o.currency) },
    { id: "payment", header: "Payment", cell: (o) => `${o.payment_type.toUpperCase()} · ${o.payment_status}` },
    { id: "fulfillment", header: "Fulfillment", cell: (o) => o.fulfillment_status },
    {
      id: "tracking",
      header: "Tracking",
      cell: (o) =>
        o.awb ? (
          <div>
            <a
              href={o.tracking_url ?? "#"}
              target="_blank"
              rel="noreferrer"
              className="text-primary hover:underline"
              onClick={(e) => e.stopPropagation()}
            >
              {o.awb}
            </a>
            <div className="text-muted-foreground text-xs">
              {[o.courier_name, o.shipment_status].filter(Boolean).join(" · ")}
            </div>
          </div>
        ) : (
          "—"
        ),
    },
  ]
  if (isCod) {
    columns.push(
      { id: "expected", header: "Expected COD", className: "text-right", cell: (o) => money(o.expected_cod_amount, o.currency) },
      {
        id: "collected",
        header: "Collected (Shiprocket)",
        className: "text-right",
        cell: (o) => (o.financial_data === "no_data" ? "Not reported" : money(o.collected_amount, o.currency)),
      },
      { id: "remitted", header: "Remitted (Shiprocket)", className: "text-right", cell: (o) => money(o.remitted_amount, o.currency) },
      { id: "pending", header: "Pending remittance", className: "text-right", cell: (o) => money(o.pending_remittance, o.currency) },
      {
        id: "settlement",
        header: "Settlement",
        cell: (o) =>
          o.settlement_date || o.settlement_reference ? (
            <div>
              <div>{o.settlement_date ? formatDate(o.settlement_date) : "—"}</div>
              <div className="text-muted-foreground text-xs">{o.settlement_reference ?? ""}</div>
            </div>
          ) : (
            "—"
          ),
      },
      {
        id: "bank",
        header: "Bank credit",
        cell: (o) =>
          o.bank_verified ? (
            <span title={o.bank_credit_reference ?? ""} className="text-emerald-600">
              Verified {money(o.bank_credit_amount, o.currency)}
            </span>
          ) : (
            <span className="text-muted-foreground">Not verified</span>
          ),
      }
    )
  }

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-end gap-3">
        <label className="text-sm">
          From
          <Input type="date" value={from} onChange={(e) => { setFrom(e.target.value); setPage(1) }} />
        </label>
        <label className="text-sm">
          To
          <Input type="date" value={to} onChange={(e) => { setTo(e.target.value); setPage(1) }} />
        </label>
        {isCod && (
          <Button
            variant="outline"
            disabled={syncMutation.isPending}
            onClick={() =>
              syncMutation.mutate(undefined, {
                onSuccess: (r) =>
                  toast.success(`Remittance sync done: ${r?.created ?? 0} new, ${r?.updated ?? 0} updated`),
                onError: () => toast.error("Remittance sync failed. Check the Shiprocket connection."),
              })
            }
          >
            <RefreshCw className="size-4" />
            {syncMutation.isPending ? "Syncing…" : "Sync from Shiprocket"}
          </Button>
        )}
      </div>

      <section className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <StatTile
          label={`Fulfilled ${label}`}
          icon={PackageCheck}
          value={(split?.fulfilled.orders ?? 0).toLocaleString("en-IN")}
          subtext={formatMoney(split?.fulfilled.amount ?? 0)}
          accent="emerald"
        />
        <StatTile
          label={`Unfulfilled ${label}`}
          icon={PackageX}
          value={(split?.unfulfilled.orders ?? 0).toLocaleString("en-IN")}
          subtext={formatMoney(split?.unfulfilled.amount ?? 0)}
          accent="amber"
        />
      </section>
      <div className="flex gap-2">
        <Button variant={fulfillment === "fulfilled" ? "default" : "outline"} onClick={() => { setFulfillment("fulfilled"); setPage(1) }}>
          View fulfilled orders
        </Button>
        <Button variant={fulfillment === "unfulfilled" ? "default" : "outline"} onClick={() => { setFulfillment("unfulfilled"); setPage(1) }}>
          View unfulfilled orders
        </Button>
      </div>

      {summaryQuery.data && !summaryQuery.data.complete && (
        <p className="rounded-md border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900">
          Figures are incomplete: {summaryQuery.data.warnings.join(" ")}
        </p>
      )}
      {summaryQuery.isError && (
        <p className="text-destructive text-sm">Could not load revenue figures.</p>
      )}

      {isCod && fin && (
        <Card>
          <CardHeader>
            <CardTitle>COD collection &amp; remittance</CardTitle>
            <p className="text-muted-foreground text-xs">
              Collected/remitted figures are what Shiprocket reports, not bank-confirmed.
              {fin.remittance_last_synced_at
                ? ` Last synced ${formatDate(fin.remittance_last_synced_at)}.`
                : " Not synced yet."}{" "}
              {fin.orders_without_shiprocket_data > 0 &&
                `${fin.orders_without_shiprocket_data} of ${fin.shipped_orders} shipped COD orders have no Shiprocket remittance data.`}
            </p>
          </CardHeader>
          <CardContent className="grid grid-cols-2 gap-4 md:grid-cols-5">
            {[
              ["Expected COD", fin.expected_amount],
              ["Collected by Shiprocket", fin.collected_amount],
              ["Remitted by Shiprocket", fin.remitted_amount],
              ["Pending remittance", fin.pending_remittance],
              ["Bank-verified credit", fin.bank_verified_amount],
            ].map(([k, v]) => (
              <div key={k}>
                <div className="text-muted-foreground text-xs">{k}</div>
                <div className="text-lg font-semibold">{formatMoney(v)}</div>
              </div>
            ))}
          </CardContent>
        </Card>
      )}

      {fulfillment && (
        <Card>
          <CardHeader className="flex flex-row flex-wrap items-center justify-between gap-2">
            <CardTitle>
              {fulfillment === "fulfilled" ? "Fulfilled" : "Unfulfilled"} {label} orders
            </CardTitle>
            <div className="flex gap-2">
              <Input
                placeholder="Search order, customer, phone, AWB"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                className="w-72"
              />
              <Button
                variant="outline"
                disabled={exportMutation.isPending}
                onClick={() =>
                  exportMutation.mutate(filters, { onError: () => toast.error("Export failed.") })
                }
              >
                <Download className="size-4" />
                Export Excel
              </Button>
            </div>
          </CardHeader>
          <CardContent>
            {ordersQuery.isError ? (
              <p className="text-destructive text-sm">Could not load orders.</p>
            ) : (
              <>
                <DataTable
                  columns={columns}
                  data={ordersQuery.data?.data ?? []}
                  rowKey={(o) => o.id}
                  onRowClick={(o) => router.push(`/orders/${o.id}`)}
                />
                {ordersQuery.data && ordersQuery.data.data.length === 0 && (
                  <p className="text-muted-foreground mt-3 text-sm">No orders match.</p>
                )}
                {ordersQuery.data?.meta.warnings?.length ? (
                  <p className="mt-3 text-xs text-amber-700">
                    {ordersQuery.data.meta.warnings.join(" ")}
                  </p>
                ) : null}
                {summaryQuery.data?.data_source === "live" && (
                  <p className="text-muted-foreground mt-3 text-xs">
                    Live Shopify data. Shipment status is not fetched per order here; open an
                    order or use the AWB link for live tracking.
                  </p>
                )}
                <div className="mt-3 flex items-center justify-between text-xs">
                  <span className="text-muted-foreground">
                    {ordersQuery.data?.meta.total_items ?? 0} orders
                  </span>
                  <div className="flex gap-2">
                    <Button size="sm" variant="outline" disabled={page <= 1} onClick={() => setPage(page - 1)}>
                      Previous
                    </Button>
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={!ordersQuery.data || page * PAGE_SIZE >= ordersQuery.data.meta.total_items}
                      onClick={() => setPage(page + 1)}
                    >
                      Next
                    </Button>
                  </div>
                </div>
              </>
            )}
          </CardContent>
        </Card>
      )}
    </div>
  )
}

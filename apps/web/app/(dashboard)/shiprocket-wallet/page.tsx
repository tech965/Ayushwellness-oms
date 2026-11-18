"use client"

import * as React from "react"
import { AlertTriangle, Download, RefreshCw, Wallet } from "lucide-react"
import { toast } from "sonner"

import { DataTable, type DataTableColumn } from "@/components/shared/data-table"
import { PageHeader } from "@/components/shared/page-header"
import { StatTile } from "@/components/shared/stat-tile"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { formatDate, formatDateTime, formatMoney } from "@/lib/format"
import {
  useCreateRechargeClaim,
  useExportWalletTransactions,
  useOrderExpenses,
  useRefreshWallet,
  useSyncWalletLedger,
  useWalletPeriods,
  useWalletSummary,
  useWalletTransactions,
  type WalletTxn,
} from "@/services/shiprocket-wallet"

const PAGE_SIZE = 25
const TYPES = [
  "recharge",
  "freight",
  "cod_charge",
  "rto_freight",
  "freight_reversal",
  "cod_reversal",
  "refund",
  "weight_discrepancy",
  "other_debit",
  "other_credit",
]

const COLUMNS: DataTableColumn<WalletTxn>[] = [
  { id: "date", header: "Date", cell: (t) => formatDateTime(t.txn_date) },
  { id: "ref", header: "Reference", cell: (t) => t.transaction_ref ?? "—" },
  { id: "type", header: "Type", cell: (t) => t.txn_type.replace(/_/g, " ") },
  {
    id: "amount",
    header: "Amount",
    className: "text-right",
    cell: (t) => (
      <span className={t.direction === "credit" ? "text-emerald-600" : ""}>
        {t.direction === "credit" ? "+" : "−"}
        {formatMoney(t.amount)}
      </span>
    ),
  },
  { id: "bal", header: "Balance after", className: "text-right", cell: (t) => (t.balance_after ? formatMoney(t.balance_after) : "—") },
  { id: "order", header: "Order", cell: (t) => t.order_number ?? "—" },
  { id: "awb", header: "AWB", cell: (t) => t.awb ?? "—" },
  { id: "courier", header: "Courier", cell: (t) => t.courier_name ?? "—" },
  { id: "desc", header: "Description", cell: (t) => t.description ?? "—" },
  {
    id: "status",
    header: "Status",
    cell: (t) => (
      <span>
        {t.status}
        {t.source !== "shiprocket_api" && (
          <span className="text-muted-foreground block text-xs">
            {t.source === "manual_claim" ? "entered manually — not counted" : t.source.replace(/_/g, " ")}
          </span>
        )}
      </span>
    ),
  },
]

function Money({ label, value, note }: { label: string; value: string | undefined; note?: string }) {
  return (
    <div>
      <div className="text-muted-foreground text-xs">{label}</div>
      <div className="text-lg font-semibold">{value === undefined ? "—" : formatMoney(value)}</div>
      {note && <div className="text-muted-foreground text-xs">{note}</div>}
    </div>
  )
}

export default function ShiprocketWalletPage() {
  const [from, setFrom] = React.useState("")
  const [to, setTo] = React.useState("")
  const [q, setQ] = React.useState("")
  const [txnType, setTxnType] = React.useState("")
  const [direction, setDirection] = React.useState<"" | "debit" | "credit">("")
  const [courier, setCourier] = React.useState("")
  const [status, setStatus] = React.useState("")
  const [by, setBy] = React.useState<"month" | "year">("month")
  const [page, setPage] = React.useState(1)
  const [orderNo, setOrderNo] = React.useState("")
  const [claimAmount, setClaimAmount] = React.useState("")
  const [claimRef, setClaimRef] = React.useState("")

  const range = {
    date_from: from ? new Date(`${from}T00:00:00`).toISOString() : undefined,
    date_to: to ? new Date(`${to}T23:59:59`).toISOString() : undefined,
  }
  const filters = {
    q,
    ...range,
    txn_type: txnType,
    direction: direction || undefined,
    courier,
    status,
  }
  const summaryQuery = useWalletSummary(range)
  const txnQuery = useWalletTransactions({ ...filters, page, pageSize: PAGE_SIZE })
  const periodsQuery = useWalletPeriods(by, range)
  const orderQuery = useOrderExpenses(orderNo, "")
  const refresh = useRefreshWallet()
  const sync = useSyncWalletLedger()
  const claim = useCreateRechargeClaim()
  const exportXlsx = useExportWalletTransactions()

  const s = summaryQuery.data
  const state = s?.state

  return (
    <>
      <PageHeader
        title="Shiprocket Wallet"
        description="Balance, shipping expenses and the wallet transaction ledger."
        actions={
          <div className="flex gap-2">
            <Button
              variant="outline"
              disabled={refresh.isPending}
              onClick={() =>
                refresh.mutate(undefined, {
                  onSuccess: () => toast.success("Wallet balance refreshed."),
                  onError: () => toast.error("Could not read the wallet balance from Shiprocket."),
                })
              }
            >
              <RefreshCw className="size-4" />
              Refresh balance
            </Button>
            <Button
              variant="outline"
              disabled={sync.isPending || state?.ledger_sync_enabled === false}
              title={state?.ledger_sync_enabled === false ? "Transaction sync is not enabled yet" : ""}
              onClick={() =>
                sync.mutate(undefined, {
                  onSuccess: (r) => toast.success(`Synced: ${r?.created ?? 0} new, ${r?.updated ?? 0} updated`),
                  onError: () => toast.error("Wallet transaction sync failed."),
                })
              }
            >
              Sync transactions
            </Button>
          </div>
        }
      />

      <div className="flex flex-col gap-6">
        {state?.low_balance && (
          <p className="flex items-center gap-2 rounded-md border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900">
            <AlertTriangle className="size-4" />
            Low balance: {state.balance ? formatMoney(state.balance) : "—"} is below the{" "}
            {formatMoney(state.low_balance_threshold)} alert threshold. Recharge in the Shiprocket
            dashboard (Billing → Wallet), then press “Refresh balance”.
          </p>
        )}
        {state && !state.ledger_sync_enabled && (
          <p className="text-muted-foreground rounded-md border p-3 text-sm">
            Transaction history sync is not enabled: Shiprocket&apos;s statement API path has not been
            verified for this account. The balance and pre-shipment protection are active; ledger
            figures below are empty until a verified statement source is configured.
          </p>
        )}

        <section className="grid grid-cols-2 gap-4 lg:grid-cols-4">
          <StatTile
            label="Current wallet balance"
            icon={Wallet}
            value={state?.balance ? formatMoney(state.balance) : "Unknown"}
            subtext={
              state?.fetched_at
                ? `${state.stale ? "Stale — " : ""}as of ${formatDateTime(state.fetched_at)}`
                : "Not fetched yet"
            }
            accent={state?.low_balance ? "orange" : "emerald"}
          />
          <StatTile label="Total recharges" icon={Wallet} value={formatMoney(s?.total_recharges ?? 0)} subtext="Confirmed only" accent="blue" />
          <StatTile label="Shipping expenses" icon={Wallet} value={formatMoney(s?.shipping_expenses ?? 0)} subtext="Freight debits" accent="amber" />
          <StatTile label="COD charges" icon={Wallet} value={formatMoney(s?.cod_charges ?? 0)} accent="amber" />
          <StatTile label="RTO expenses" icon={Wallet} value={formatMoney(s?.rto_expenses ?? 0)} accent="orange" />
          <StatTile label="Credits & reversals" icon={Wallet} value={formatMoney(s?.credits_and_reversals ?? 0)} subtext="Excludes recharges" accent="emerald" />
          <StatTile label="Pending transactions" icon={Wallet} value={String(s?.pending_count ?? 0)} subtext={formatMoney(s?.pending_amount ?? 0)} accent="slate" />
          <StatTile label="Failed transactions" icon={Wallet} value={String(s?.failed_count ?? 0)} subtext={formatMoney(s?.failed_amount ?? 0)} accent="slate" />
        </section>

        <Card>
          <CardHeader>
            <CardTitle>Ledger</CardTitle>
            <p className="text-muted-foreground text-xs">
              Only amounts Shiprocket reported. Estimated courier charges are never included.
            </p>
          </CardHeader>
          <CardContent className="grid grid-cols-2 gap-4 md:grid-cols-5">
            <Money label="Opening balance" value={s?.opening_balance ?? undefined} note={s?.opening_balance ? undefined : "Not available"} />
            <Money label="Closing balance" value={s?.closing_balance ?? undefined} note={s?.closing_balance ? undefined : "Not available"} />
            <Money label="Net shipping cost" value={s?.net_shipping_cost} note="Expenses − reversals" />
            <Money label="Weight discrepancy" value={s?.weight_discrepancy_charges} />
            <div>
              <div className="text-muted-foreground text-xs">Ledger check</div>
              <div className="text-sm">
                {s?.ledger_consistent === null || s?.ledger_consistent === undefined
                  ? "Not enough balance data"
                  : s.ledger_consistent
                    ? "Balances reconcile"
                    : `Gap of ${formatMoney(s.ledger_gap ?? 0)}`}
              </div>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Record a recharge</CardTitle>
            <p className="text-muted-foreground text-xs">
              Recharge in the Shiprocket dashboard (Billing → Wallet → Add Money) — this OMS never
              initiates payments. Entries here stay <b>unverified</b> and are not counted until
              Shiprocket&apos;s own balance shows the increase.
              {s && s.unverified_recharge_claims > 0 && ` ${s.unverified_recharge_claims} unverified.`}
            </p>
          </CardHeader>
          <CardContent className="flex flex-wrap gap-2">
            <Input className="w-40" placeholder="Amount" value={claimAmount} onChange={(e) => setClaimAmount(e.target.value)} />
            <Input className="w-64" placeholder="Payment reference" value={claimRef} onChange={(e) => setClaimRef(e.target.value)} />
            <Button
              variant="outline"
              disabled={!claimAmount || !claimRef || claim.isPending}
              onClick={() =>
                claim.mutate(
                  { amount: claimAmount, payment_reference: claimRef },
                  {
                    onSuccess: () => {
                      toast.success("Recorded as unverified.")
                      setClaimAmount("")
                      setClaimRef("")
                    },
                    onError: () => toast.error("Could not record the recharge."),
                  }
                )
              }
            >
              Record (unverified)
            </Button>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex flex-row flex-wrap items-center justify-between gap-2">
            <CardTitle>Transactions</CardTitle>
            <Button
              variant="outline"
              disabled={exportXlsx.isPending}
              onClick={() => exportXlsx.mutate(filters, { onError: () => toast.error("Export failed.") })}
            >
              <Download className="size-4" />
              Export Excel
            </Button>
          </CardHeader>
          <CardContent className="flex flex-col gap-3">
            <div className="flex flex-wrap gap-2">
              <Input className="w-64" placeholder="Order no., AWB or transaction ref" value={q} onChange={(e) => { setQ(e.target.value); setPage(1) }} />
              <Input className="w-40" type="date" value={from} onChange={(e) => { setFrom(e.target.value); setPage(1) }} />
              <Input className="w-40" type="date" value={to} onChange={(e) => { setTo(e.target.value); setPage(1) }} />
              <select className="border-input h-9 rounded-md border bg-transparent px-2 text-sm" value={txnType} onChange={(e) => { setTxnType(e.target.value); setPage(1) }}>
                <option value="">All types</option>
                {TYPES.map((t) => (<option key={t} value={t}>{t.replace(/_/g, " ")}</option>))}
              </select>
              <select className="border-input h-9 rounded-md border bg-transparent px-2 text-sm" value={direction} onChange={(e) => { setDirection(e.target.value as "" | "debit" | "credit"); setPage(1) }}>
                <option value="">Debit & credit</option>
                <option value="debit">Debit</option>
                <option value="credit">Credit</option>
              </select>
              <Input className="w-40" placeholder="Courier" value={courier} onChange={(e) => { setCourier(e.target.value); setPage(1) }} />
              <select className="border-input h-9 rounded-md border bg-transparent px-2 text-sm" value={status} onChange={(e) => { setStatus(e.target.value); setPage(1) }}>
                <option value="">Any status</option>
                <option value="confirmed">Confirmed</option>
                <option value="pending">Pending</option>
                <option value="failed">Failed</option>
                <option value="unverified">Unverified</option>
              </select>
            </div>
            <DataTable columns={COLUMNS} data={txnQuery.data?.data ?? []} rowKey={(t) => t.id} />
            {txnQuery.data && txnQuery.data.data.length === 0 && (
              <p className="text-muted-foreground text-sm">No transactions match.</p>
            )}
            <div className="flex items-center justify-between text-xs">
              <span className="text-muted-foreground">{txnQuery.data?.meta.total_items ?? 0} transactions</span>
              <div className="flex gap-2">
                <Button size="sm" variant="outline" disabled={page <= 1} onClick={() => setPage(page - 1)}>Previous</Button>
                <Button size="sm" variant="outline" disabled={!txnQuery.data || page * PAGE_SIZE >= txnQuery.data.meta.total_items} onClick={() => setPage(page + 1)}>Next</Button>
              </div>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex flex-row items-center justify-between">
            <CardTitle>{by === "month" ? "Monthly" : "Yearly"} summary</CardTitle>
            <Button size="sm" variant="outline" onClick={() => setBy(by === "month" ? "year" : "month")}>
              Show {by === "month" ? "yearly" : "monthly"}
            </Button>
          </CardHeader>
          <CardContent>
            <table className="w-full text-sm">
              <thead className="text-muted-foreground text-left text-xs">
                <tr><th>Period</th><th className="text-right">Recharges</th><th className="text-right">Shipping</th><th className="text-right">COD</th><th className="text-right">RTO</th><th className="text-right">Reversals</th><th className="text-right">Net cost</th></tr>
              </thead>
              <tbody>
                {(periodsQuery.data ?? []).map((p) => (
                  <tr key={p.period} className="border-t">
                    <td>{p.period}</td>
                    <td className="text-right">{formatMoney(p.recharges)}</td>
                    <td className="text-right">{formatMoney(p.shipping_expenses)}</td>
                    <td className="text-right">{formatMoney(p.cod_charges)}</td>
                    <td className="text-right">{formatMoney(p.rto_expenses)}</td>
                    <td className="text-right">{formatMoney(p.credits_and_reversals)}</td>
                    <td className="text-right">{formatMoney(p.net_shipping_cost)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {(periodsQuery.data ?? []).length === 0 && (
              <p className="text-muted-foreground text-sm">No ledger data for this range.</p>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Order-level expenses</CardTitle>
            <p className="text-muted-foreground text-xs">
              Actual wallet movements for an order, shown apart from pre-shipment estimates.
            </p>
          </CardHeader>
          <CardContent className="flex flex-col gap-3">
            <Input className="w-64" placeholder="Order number" value={orderNo} onChange={(e) => setOrderNo(e.target.value)} />
            {orderQuery.data && (
              <div className="flex flex-col gap-2 text-sm">
                <div className="flex flex-wrap gap-6">
                  <Money label="Actual debits" value={orderQuery.data.actual.total_debits} />
                  <Money label="Actual credits/reversals" value={orderQuery.data.actual.total_credits} />
                  <Money label="Actual net cost" value={orderQuery.data.actual.net_cost} />
                </div>
                <DataTable columns={COLUMNS} data={orderQuery.data.actual.transactions} rowKey={(t) => t.id} />
                <p className="text-muted-foreground text-xs">
                  {orderQuery.data.estimated.length} pre-shipment estimate(s) recorded
                  {orderQuery.data.estimated[0]?.estimated_charge
                    ? ` (latest ${formatMoney(orderQuery.data.estimated[0].estimated_charge)}, not a charge)`
                    : ""}
                  . {orderQuery.data.note}
                </p>
              </div>
            )}
          </CardContent>
        </Card>
        <p className="text-muted-foreground text-xs">Dates shown in IST. {formatDate(new Date().toISOString())}</p>
      </div>
    </>
  )
}

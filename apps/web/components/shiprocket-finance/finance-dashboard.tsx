"use client"

import * as React from "react"
import { Download, FileUp, RefreshCw } from "lucide-react"
import { toast } from "sonner"

import { DrilldownTotals } from "@/components/remittance/drilldown-toolbar"
import {
  DateRangePicker,
  type DateRangeValue,
} from "@/components/shared/date-range-picker"
import type { DataTableColumn } from "@/components/shared/data-table"
import { DataDrilldown } from "@/components/shared/data-drilldown"
import { useDebouncedValue } from "@/components/shared/order-drilldown"
import { PageHeader } from "@/components/shared/page-header"
import { QueryStates } from "@/components/shared/query-states"
import { SelectableStatCard } from "@/components/shared/selectable-stat-card"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { getApiErrorMessage } from "@/lib/api-client"
import { useAuth } from "@/lib/auth-context"
import { formatDate, formatDateTime, formatMoney } from "@/lib/format"
import { useDateRangeFilter } from "@/lib/use-date-range-filter"
import { usePaginationState } from "@/lib/use-pagination"
import {
  downloadFinanceExport,
  useFinanceRecords,
  useFinanceSources,
  useFinanceSummary,
  useShipmentCharges,
  useSyncOrderFinance,
} from "@/services/shiprocket-finance"
import type {
  ExpenseRow,
  ExpenseSummary,
  FinanceCard,
  FinanceFilters,
  FinanceKind,
  FinanceSource,
  PayoutRow,
  PayoutSummary,
} from "@/types/shiprocket-finance"

import { PayoutDetailDialog } from "./payout-detail-dialog"
import { ReportImportDialog } from "./report-import-dialog"

const money = (value: string | null | undefined) =>
  value === null || value === undefined ? "—" : formatMoney(value)
const day = (value: string | null | undefined) => (value ? formatDate(value) : "—")

const ACCENT = ["blue", "green", "orange", "violet", "amber", "red", "slate"] as const

const METHOD_LABEL: Record<FinanceSource["method"], string> = {
  api: "Automatic (API)",
  report: "Report import",
  not_available: "Not available",
  not_ingested: "Not imported",
}
const STATE_LABEL: Record<FinanceSource["state"], string> = {
  ready: "Ready",
  not_configured: "Shiprocket not configured",
  not_enabled: "API path not enabled yet",
  report_only: "Report only",
  not_available: "No supported source",
  not_ingested: "Not imported",
}

/** Where each figure comes from, so nobody mistakes a report-only field for a
 * live API feed or a missing feed for a zero.
 */
function SourcesPanel({ kind }: { kind: FinanceKind }) {
  const query = useFinanceSources(kind)
  return (
    <details className="bg-card rounded-lg border p-3 text-sm" data-testid="sources">
      <summary className="cursor-pointer font-medium">
        Where this data comes from (API vs report)
      </summary>
      <QueryStates
        isLoading={query.isLoading}
        isError={query.isError}
        error={query.error}
        data={query.data}
        onRetry={() => void query.refetch()}
      >
        {(sources) => (
          <ul className="mt-2 space-y-2">
            {sources.map((s) => (
              <li key={s.data} className="flex flex-wrap items-start gap-2">
                <Badge variant="outline" className="shrink-0">
                  {METHOD_LABEL[s.method]}
                </Badge>
                <span className="min-w-0 flex-1">
                  <span className="font-medium">{s.data}</span>
                  <span className="text-muted-foreground block text-xs">
                    {STATE_LABEL[s.state]}
                    {s.endpoint ? ` · ${s.endpoint}` : ""} — {s.note}
                  </span>
                </span>
              </li>
            ))}
          </ul>
        )}
      </QueryStates>
    </details>
  )
}

const EXPENSE_COLUMNS: DataTableColumn<ExpenseRow>[] = [
  { id: "date", header: "Date", cell: (r) => formatDateTime(r.txn_date) },
  { id: "ref", header: "Transaction ID", cell: (r) => r.transaction_ref ?? "—" },
  { id: "type", header: "Type", cell: (r) => r.txn_type.replaceAll("_", " ") },
  {
    id: "amount",
    header: "Amount",
    className: "text-right",
    cell: (r) => `${r.direction === "credit" ? "+" : "−"}${money(r.amount)}`,
  },
  {
    id: "contribution",
    header: "In total",
    className: "text-right",
    cell: (r) => <span className="font-semibold">{money(r.contribution)}</span>,
  },
  { id: "order", header: "Order", cell: (r) => r.order_number ?? "—" },
  { id: "awb", header: "AWB", cell: (r) => r.awb ?? "—" },
  { id: "courier", header: "Courier", cell: (r) => r.courier_name ?? "—" },
  { id: "desc", header: "Description", cell: (r) => r.description ?? "—" },
  {
    id: "status",
    header: "Status",
    cell: (r) => (r.source === "report_import" ? `${r.status} · from report` : r.status),
  },
]

const PAYOUT_COLUMNS: DataTableColumn<PayoutRow>[] = [
  { id: "crf", header: "CRF / payout ID", cell: (r) => r.crf_id ?? "—" },
  { id: "utr", header: "UTR", cell: (r) => r.utr ?? "—" },
  { id: "date", header: "Payout date", cell: (r) => day(r.payout_date) },
  {
    id: "net",
    header: "Remittance amount",
    className: "text-right",
    cell: (r) => money(r.net_amount),
  },
  {
    id: "contribution",
    header: "In total",
    className: "text-right",
    cell: (r) => <span className="font-semibold">{money(r.contribution)}</span>,
  },
  {
    id: "cod",
    header: "COD amount",
    className: "text-right",
    cell: (r) => money(r.cod_amount),
  },
  {
    id: "freight",
    header: "Freight deducted",
    className: "text-right",
    cell: (r) => money(r.freight_deduction),
  },
  {
    id: "other",
    header: "Other deductions",
    className: "text-right",
    cell: (r) => money(r.other_deductions),
  },
  { id: "status", header: "Status (reported)", cell: (r) => r.status ?? "—" },
  {
    id: "bank",
    header: "Bank comparison",
    cell: (r) => (
      <span>
        {r.bank_state_label}
        {r.bank_amount !== null && (
          <span className="text-muted-foreground block text-xs">
            Bank {money(r.bank_amount)}
          </span>
        )}
      </span>
    ),
  },
]

const BANK_STATES = [
  ["matched", "Matched to bank"],
  ["differs", "Bank amount differs"],
  ["not_found", "Not found in bank"],
  ["no_utr", "No UTR yet"],
] as const

interface Props {
  kind: FinanceKind
}

/** Shiprocket Expenses (wallet movements) and Shiprocket COD Remittances
 * (payout reports + UTR comparison with the bank). Every card opens the exact
 * records behind it, with search, date and status filters, pagination and
 * Excel export. Figures that no Shiprocket API provides are labelled as report
 * imports. Cashfree settlements are a different module and never appear here.
 */
export function FinanceDashboard({ kind }: Props) {
  const { hasPermission } = useAuth()
  const isExpenses = kind === "expenses"
  const canImport = hasPermission("reconciliation.upload")
  const canSync = hasPermission("reconciliation.manage")
  const { range, setRange, dateFrom, dateTo } = useDateRangeFilter()
  const [selected, setSelected] = React.useState<FinanceCard | null>(null)
  const [search, setSearch] = React.useState("")
  const debounced = useDebouncedValue(search)
  const [courier, setCourier] = React.useState("")
  const [status, setStatus] = React.useState("")
  const [bankState, setBankState] = React.useState("")
  const [importing, setImporting] = React.useState(false)
  const [viewing, setViewing] = React.useState<string | null>(null)
  const [exporting, setExporting] = React.useState(false)
  const { page, pageSize, setPage, resetPage } = usePaginationState()
  const chargeSearch = useDebouncedValue(search)
  const [chargePage, setChargePage] = React.useState(1)

  const filters: FinanceFilters = {
    date_from: dateFrom,
    date_to: dateTo,
    courier: courier || undefined,
    status: status || undefined,
    bank_state: bankState || undefined,
  }
  const drillFilters = { ...filters, q: debounced || undefined }
  const summary = useFinanceSummary<ExpenseSummary & PayoutSummary>(kind, filters)
  const records = useFinanceRecords<ExpenseRow & PayoutRow>({
    kind,
    kpi: selected?.key,
    filters: drillFilters,
    page,
    pageSize,
  })
  const charges = useShipmentCharges(chargeSearch, chargePage, isExpenses)
  const sync = useSyncOrderFinance()

  function change(update: () => void) {
    update()
    resetPage()
  }

  function pick(card: FinanceCard) {
    setSelected(selected?.key === card.key ? null : card)
    setSearch("")
    resetPage()
  }

  async function runSync() {
    try {
      const result = await sync.mutateAsync()
      if (result.status === "access_denied")
        toast.error(result.message ?? "Access denied.")
      else
        toast.success(
          `Checked ${result.checked} of ${result.candidates} orders: ` +
            `${result.with_charges} with charges, ${result.with_remittance_reference} with a UTR.`
        )
    } catch (error) {
      toast.error(getApiErrorMessage(error))
    }
  }

  async function exportRecords() {
    if (!selected) return
    setExporting(true)
    try {
      await downloadFinanceExport(kind, selected.key, drillFilters)
    } catch (error) {
      toast.error(getApiErrorMessage(error))
    } finally {
      setExporting(false)
    }
  }

  const pageAmount =
    (records.data?.data ?? []).reduce(
      (sum, row) => sum + Math.round(Number(row.contribution ?? 0) * 100),
      0
    ) / 100

  return (
    <div className="space-y-5">
      <PageHeader
        title={isExpenses ? "Shiprocket Expenses" : "Shiprocket COD Remittances"}
        description={
          isExpenses
            ? "Shipping cost actually debited from the Shiprocket wallet. Click any card to see the transactions behind it."
            : "COD payouts reported by Shiprocket, compared with bank statement credits by UTR. Click any card to see the payouts behind it. Cashfree settlements are separate."
        }
        actions={
          <div className="flex flex-wrap gap-2">
            {canImport && (
              <Button size="sm" variant="outline" onClick={() => setImporting(true)}>
                <FileUp className="size-4" />
                {isExpenses ? "Import wallet report" : "Import payout report"}
              </Button>
            )}
            {isExpenses && canSync && (
              <Button
                size="sm"
                variant="outline"
                disabled={sync.isPending}
                onClick={() => void runSync()}
              >
                <RefreshCw className="size-4" />
                Fetch shipment charges
              </Button>
            )}
          </div>
        }
      />

      <div className="flex flex-wrap items-center gap-2" data-testid="finance-filters">
        <DateRangePicker
          value={range}
          onChange={(value: DateRangeValue) => change(() => setRange(value))}
        />
        {isExpenses ? (
          <select
            aria-label="Courier"
            className="bg-background h-9 rounded-md border px-2 text-sm"
            value={courier}
            onChange={(e) => change(() => setCourier(e.target.value))}
          >
            <option value="">All couriers</option>
            {(summary.data?.couriers ?? []).map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </select>
        ) : (
          <>
            <select
              aria-label="Payout status"
              className="bg-background h-9 rounded-md border px-2 text-sm"
              value={status}
              onChange={(e) => change(() => setStatus(e.target.value))}
            >
              <option value="">All statuses</option>
              {(summary.data?.statuses ?? []).map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </select>
            <select
              aria-label="Bank comparison"
              className="bg-background h-9 rounded-md border px-2 text-sm"
              value={bankState}
              onChange={(e) => change(() => setBankState(e.target.value))}
            >
              <option value="">Any bank result</option>
              {BANK_STATES.map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </>
        )}
      </div>

      <SourcesPanel kind={kind} />

      <QueryStates
        isLoading={summary.isLoading}
        isError={summary.isError}
        error={summary.error}
        data={summary.data}
        onRetry={() => void summary.refetch()}
      >
        {(data) => (
          <>
            {!isExpenses && !data.bank_data_available && (
              <p className="rounded-lg border border-amber-500/50 bg-amber-500/10 p-3 text-sm">
                No bank statement has been imported, so nothing can be compared with the
                bank. Upload one under COD Remittance Reconciliation.
              </p>
            )}
            <div
              className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-5"
              data-testid="kpi-grid"
            >
              {data.cards.map((card, index) => (
                <SelectableStatCard
                  key={card.key}
                  label={card.label}
                  value={money(card.amount)}
                  detail={`${card.count} ${isExpenses ? "transactions" : "payouts"}`}
                  description={card.definition}
                  tooltip={card.definition}
                  accent={ACCENT[index % ACCENT.length]}
                  selected={selected?.key === card.key}
                  onClick={() => pick(card)}
                />
              ))}
            </div>
          </>
        )}
      </QueryStates>

      {selected &&
        (isExpenses ? (
          <DataDrilldown<ExpenseRow>
            title={selected.label}
            description="Same date range and filters as the cards above."
            query={records as never}
            columns={EXPENSE_COLUMNS}
            rowKey={(row) => row.id}
            header={
              <DrillHeader
                records={records.data}
                card={selected}
                searching={Boolean(debounced)}
                pageAmount={pageAmount}
                exporting={exporting}
                onExport={() => void exportRecords()}
              />
            }
            search={search}
            onSearchChange={(value) => {
              setSearch(value)
              resetPage()
            }}
            onPageChange={setPage}
            onClear={() => setSelected(null)}
            searchPlaceholder="Search order, AWB or transaction ID"
          />
        ) : (
          <DataDrilldown<PayoutRow>
            title={selected.label}
            description="Click a payout for its bank credits and AWB lines."
            query={records as never}
            columns={PAYOUT_COLUMNS}
            rowKey={(row) => row.id}
            onRowClick={(row) => setViewing(row.id)}
            header={
              <DrillHeader
                records={records.data}
                card={selected}
                searching={Boolean(debounced)}
                pageAmount={pageAmount}
                exporting={exporting}
                onExport={() => void exportRecords()}
              />
            }
            search={search}
            onSearchChange={(value) => {
              setSearch(value)
              resetPage()
            }}
            onPageChange={setPage}
            onClear={() => setSelected(null)}
            searchPlaceholder="Search CRF ID or UTR"
          />
        ))}

      {isExpenses && (
        <section className="space-y-2" aria-label="Shipment charges">
          <h3 className="text-base font-semibold">
            Charges per shipment (Shiprocket order data)
          </h3>
          <p className="text-muted-foreground text-xs">
            Freight and COD fee Shiprocket reports on each order. Informational: these are
            never added to the wallet totals above, which would count freight twice.
          </p>
          <Input
            aria-label="Search shipment charges"
            placeholder="Search AWB or order"
            value={search}
            onChange={(e) => {
              setSearch(e.target.value)
              setChargePage(1)
            }}
            className="max-w-xs"
          />
          <QueryStates
            isLoading={charges.isLoading}
            isError={charges.isError}
            error={charges.error}
            data={charges.data}
            onRetry={() => void charges.refetch()}
            isEmpty={(d) => d.data.length === 0}
            emptyTitle="No shipment charges fetched yet"
            emptyDescription="Use “Fetch shipment charges”. Only orders with a known Shiprocket order id can be fetched."
          >
            {(result) => (
              <div className="space-y-2">
                <p className="text-muted-foreground text-xs">
                  {result.coverage.with_charges} of {result.coverage.orders_checked}{" "}
                  checked orders report charges.
                </p>
                <div className="bg-card overflow-x-auto rounded-lg border">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="text-left">
                        {[
                          "AWB",
                          "Order",
                          "Zone",
                          "Freight",
                          "COD fee",
                          "Weight (applied / charged)",
                          "Fetched",
                        ].map((h) => (
                          <th key={h} className="p-2 font-medium">
                            {h}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {result.data.map((c) => (
                        <tr key={c.awb} className="border-t">
                          <td className="p-2">{c.awb}</td>
                          <td className="p-2">{c.order_number ?? "—"}</td>
                          <td className="p-2">{c.zone ?? "—"}</td>
                          <td className="p-2">{money(c.freight_charges)}</td>
                          <td className="p-2">{money(c.cod_charges)}</td>
                          <td className="p-2">
                            {c.applied_weight ?? "—"} / {c.charged_weight ?? "—"}
                          </td>
                          <td className="p-2">{formatDateTime(c.fetched_at)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                {result.meta.total_pages > 1 && (
                  <div className="flex gap-2">
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={chargePage <= 1}
                      onClick={() => setChargePage(chargePage - 1)}
                    >
                      Previous
                    </Button>
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={chargePage >= result.meta.total_pages}
                      onClick={() => setChargePage(chargePage + 1)}
                    >
                      Next
                    </Button>
                  </div>
                )}
              </div>
            )}
          </QueryStates>
        </section>
      )}

      {importing && (
        <ReportImportDialog kind={kind} onClose={() => setImporting(false)} />
      )}
      {viewing && <PayoutDetailDialog id={viewing} onClose={() => setViewing(null)} />}
    </div>
  )
}

function DrillHeader({
  records,
  card,
  searching,
  pageAmount,
  exporting,
  onExport,
}: {
  records: { totals: import("@/types/remittance").DrillTotals } | undefined
  card: FinanceCard
  searching: boolean
  pageAmount: number
  exporting: boolean
  onExport: () => void
}) {
  return (
    <div className="space-y-2">
      <Button size="sm" variant="outline" onClick={onExport} disabled={exporting}>
        <Download className="size-4" />
        {exporting ? "Exporting…" : "Export these records"}
      </Button>
      <DrilldownTotals
        totals={records?.totals}
        card={{ count: card.count, amount: card.amount }}
        definition={card.definition}
        searching={searching}
        pageAmount={pageAmount}
      />
    </div>
  )
}

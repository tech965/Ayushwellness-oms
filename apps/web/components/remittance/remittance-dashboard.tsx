"use client"

import * as React from "react"
import { Download, FileUp, Landmark, RefreshCw } from "lucide-react"
import { toast } from "sonner"

import { ClickableBarChart, ClickableDonut } from "@/components/shared/clickable-charts"
import type { DataTableColumn } from "@/components/shared/data-table"
import { DataDrilldown } from "@/components/shared/data-drilldown"
import { FilterBar } from "@/components/shared/filter-bar"
import { useDebouncedValue } from "@/components/shared/order-drilldown"
import { PageHeader } from "@/components/shared/page-header"
import { QueryStates } from "@/components/shared/query-states"
import { SelectableStatCard } from "@/components/shared/selectable-stat-card"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { getApiErrorMessage } from "@/lib/api-client"
import { useAuth } from "@/lib/auth-context"
import { formatDate } from "@/lib/format"
import { formatPercent } from "@/lib/percent"
import { definitionOf } from "@/lib/remittance-definitions"
import {
  percentageBasis,
  percentageText,
  rupees,
  signedRupees,
  STATUS_STYLE,
} from "@/lib/remittance-format"
import { cn } from "@/lib/utils"
import { useDateRangeFilter } from "@/lib/use-date-range-filter"
import { usePaginationState } from "@/lib/use-pagination"
import {
  downloadDrilldownExport,
  downloadRemittanceExport,
  useRematch,
  useRemittanceRecords,
  useRemittanceReport,
  useRemittanceSummary,
  useRemittanceUnmatched,
} from "@/services/remittance"
import type {
  CodRecord,
  GatewayRow,
  PartnerRow,
  PrepaidRecord,
  RemittanceFilters,
  RemittanceModule,
  RemittanceStatus,
  RemittanceSummary,
  UnmatchedRow,
} from "@/types/remittance"

import { AdjustDialog } from "./adjust-dialog"
import { BankPositionPanel } from "./bank-position"
import { CashfreeSyncPanel } from "./cashfree-sync-panel"
import { DrilldownFilters, DrilldownTotals } from "./drilldown-toolbar"
import { ManualMatchDialog } from "./manual-match-dialog"
import { RecordDetailDialog, UnmatchedDetailDialog } from "./record-detail-dialog"
import { ReconciliationTabs } from "./reconciliation-tabs"
import { RemittanceReports } from "./remittance-reports"
import { UploadDialog, type UploadKind } from "./upload-dialog"

const ALL = "__all__"

export type Selection =
  | { type: "kpi"; key: string; title: string }
  | { type: "status"; key: RemittanceStatus }
  | { type: "partner"; key: string; kpi?: string; title?: string }
  | {
      type: "unmatched"
      kind?: string
      title: string
      partner?: string
      /** Set when a KPI card (or its percentage) opened the list. */
      card?: string
    }
  | { type: "age"; bucket: string }
  | { type: "settled"; day: string }

interface KpiCard {
  key: string
  label: string
  value: string
  detail: string
  description: string
  /** Records selector, or "unmatched" to open the unmatched list. */
  drill: string
  /** The secondary row of figures (still clickable). */
  supporting?: boolean
  accent: "blue" | "green" | "orange" | "violet" | "amber" | "red" | "slate"
}

const COD_STATUSES: RemittanceStatus[] = [
  "pending",
  "partially_reconciled",
  "matched",
  "short",
  "excess",
]
const PREPAID_STATUSES: RemittanceStatus[] = [
  "pending",
  "partially_reconciled",
  "matched",
  "short",
  "excess",
  "failed",
  "refunded",
]

function num(summary: RemittanceSummary, key: string, field: "count" | "amount") {
  const kpi = summary.kpis[key]
  return kpi ? kpi[field] : field === "count" ? 0 : "0"
}

function share(count: number | string, denominator: number | string): string {
  const d = Number(denominator)
  return d > 0 ? formatPercent((Number(count) / d) * 100) : "—"
}

/** Selector -> the summary KPI whose figure its drilldown must reconcile to. */
const KPI_OF_DRILL: Record<RemittanceModule, Record<string, string>> = {
  cod: {
    delivered: "delivered_orders",
    expected: "expected_collection",
    received: "remittance_received",
    bank_confirmed: "bank_confirmed",
    pending: "pending",
    short: "short",
    excess: "excess",
    unmatched: "unmatched",
    unmatched_bank_credits: "unmatched_bank_credits",
    reconciled: "reconciled",
    bank_remaining: "bank_remaining",
    partially_confirmed: "partially_confirmed",
    adjusted: "adjusted",
    payment_success: "payment_success",
    payment_initiated: "payment_initiated",
    payment_pending: "payment_pending",
  },
  prepaid: {
    orders: "total_prepaid_orders",
    gross: "gross_collection",
    charges: "gateway_charges",
    gst: "gst",
    refunds: "refunds",
    expected_net: "expected_net",
    reported_settlement: "reported_settlement",
    bank_settlement: "bank_settlement",
    difference: "settlement_difference",
    pending: "pending",
    unmatched: "unmatched",
    unmatched_bank_credits: "unmatched_bank_credits",
    reconciled: "reconciled",
    bank_remaining: "bank_remaining",
    partially_confirmed: "partially_confirmed",
    adjusted: "adjusted",
    unverified: "unverified",
  },
}

/** "statement-matched ₹a · manually verified ₹b" for a bank-confirmed card. */
function evidenceSplit(s: RemittanceSummary, key: string): string {
  const kpi = s.kpis[key]
  return `statement ${rupees(String(kpi?.statement_matched ?? 0))} · manual ${rupees(String(kpi?.manually_verified ?? 0))}`
}

/** The two unmatched cards: which list each opens. They are kept apart because
 * one is courier / gateway-reported money and the other is bank money.
 */
function unmatchedKindOf(module: RemittanceModule, drill: string): string | undefined {
  if (drill === "unmatched_bank_credits") return "bank_credit"
  if (drill === "unmatched") {
    return module === "cod" ? "remittance_line" : "gateway_line"
  }
  return undefined
}

function unmatchedCreditsCard(s: RemittanceSummary): KpiCard {
  return {
    key: "unmatched_bank_credits",
    label: "Unmatched Bank Credits",
    value: String(Number(num(s, "unmatched_bank_credits", "count"))),
    detail: `${rupees(String(num(s, "unmatched_bank_credits", "amount")))} not linked to any reference`,
    description: "Bank money not yet explained (shared by COD and prepaid)",
    drill: "unmatched_bank_credits",
    accent: "orange",
    supporting: true,
  }
}

function completionCard(s: RemittanceSummary, description: string): KpiCard {
  const comp = s.percentages.find((p) => p.key === "completion")
  return {
    key: "completion",
    label: "Reconciliation Completion",
    value: comp ? percentageText(comp) : "—",
    detail: comp
      ? `${comp.numerator} of ${comp.denominator} ${comp.denominator_label}`
      : "—",
    description,
    drill: "reconciled",
    accent: "blue",
    supporting: true,
  }
}

function codCards(s: RemittanceSummary): KpiCard[] {
  const n = Number(s.denominators.delivered_orders)
  const expected = String(s.denominators.expected_amount)
  const count = (k: string) => Number(num(s, k, "count"))
  const amt = (k: string) => String(num(s, k, "amount"))
  return [
    {
      key: "expected",
      label: "Total COD to be Settled",
      value: rupees(expected),
      detail: `${count("expected_collection")} delivered COD orders`,
      description: "COD amount of every delivered COD order",
      drill: "expected",
      accent: "violet",
    },
    {
      key: "received",
      label: "Total COD Settled",
      value: rupees(amt("remittance_received")),
      detail: `${count("remittance_received")} orders · ${share(count("remittance_received"), n)} of delivered · COD bank confirmed ${rupees(amt("bank_confirmed"))}`,
      description: "Courier-reported remittance. Not proof of a bank credit",
      drill: "received",
      accent: "green",
    },
    {
      key: "pending",
      label: "Pending Settlement",
      value: rupees(amt("pending")),
      detail: `${count("pending")} orders · ${share(count("pending"), n)} of delivered`,
      description: "Delivered, with no courier-reported remittance yet",
      drill: "pending",
      accent: "amber",
    },
    {
      key: "short",
      label: "Short Settlement",
      value: rupees(amt("short")),
      detail: `${count("short")} orders · ${share(amt("short"), expected)} of expected`,
      description: "Courier-reported remittance is less than the COD amount",
      drill: "short",
      accent: "red",
    },
    {
      key: "excess",
      label: "Excess Settlement",
      value: rupees(amt("excess")),
      detail: `${count("excess")} orders · ${share(amt("excess"), expected)} of expected`,
      description: "Courier-reported remittance is more than the COD amount",
      drill: "excess",
      accent: "violet",
    },
    {
      key: "unmatched",
      label: "Unmatched Remittances",
      value: String(count("unmatched")),
      detail: `${rupees(amt("unmatched"))} courier-reported`,
      description: "Remittance lines with no delivered COD order",
      drill: "unmatched",
      accent: "orange",
    },
    {
      key: "reconciled",
      label: "Reconciled Orders",
      value: String(count("reconciled")),
      detail: `${rupees(amt("reconciled"))} expected COD`,
      description: "COD settled equals COD amount and is bank-confirmed",
      drill: "reconciled",
      accent: "green",
    },
    {
      key: "delivered",
      label: "Total COD Delivered Orders",
      value: n.toLocaleString("en-IN"),
      detail: `${rupees(expected)} to collect`,
      description: "Delivered orders paid by cash on delivery",
      drill: "delivered",
      accent: "blue",
      supporting: true,
    },
    {
      key: "bank",
      label: "COD Bank Confirmed",
      value: rupees(amt("bank_confirmed")),
      detail: `${count("bank_confirmed")} orders · ${share(amt("bank_confirmed"), expected)} of expected`,
      description: `Actual bank credits: ${evidenceSplit(s, "bank_confirmed")}`,
      drill: "bank_confirmed",
      accent: "green",
      supporting: true,
    },
    unmatchedCreditsCard(s),
    completionCard(s, "Fully reconciled ÷ delivered COD orders"),
  ]
}

/** The 5-KPI "Payment status" summary. "Total COD Orders" is rendered
 * separately (not via `SelectableStatCard`) since it spans every COD order
 * in the period, not just delivered ones -- a different population from
 * everything else this page drills into, so it has no accurate drilldown
 * here and stays display-only, per `total_cod_orders`'s definition.
 */
function paymentStatusCards(s: RemittanceSummary): KpiCard[] {
  const delivered = Number(s.denominators.delivered_orders)
  const count = (k: string) => Number(num(s, k, "count"))
  const amt = (k: string) => String(num(s, k, "amount"))
  return [
    {
      key: "cod_delivered_orders",
      label: "COD Delivered Orders",
      value: delivered.toLocaleString("en-IN"),
      detail: `${rupees(String(s.denominators.expected_amount))} to collect`,
      description: "Delivered orders paid by cash on delivery",
      drill: "delivered",
      accent: "blue",
    },
    {
      key: "payment_success",
      label: "Payment Success",
      value: count("payment_success").toLocaleString("en-IN"),
      detail: `${rupees(amt("payment_success"))} · ${share(count("payment_success"), delivered)} of delivered`,
      description: "Fully remitted by the courier and bank-confirmed",
      drill: "payment_success",
      accent: "green",
    },
    {
      key: "payment_initiated",
      label: "Payment Initiated",
      value: count("payment_initiated").toLocaleString("en-IN"),
      detail: `${rupees(amt("payment_initiated"))} · ${share(count("payment_initiated"), delivered)} of delivered`,
      description: "Reported by the courier, not yet a clean bank-confirmed match",
      drill: "payment_initiated",
      accent: "amber",
    },
    {
      key: "payment_pending",
      label: "Payment Pending",
      value: count("payment_pending").toLocaleString("en-IN"),
      detail: `${rupees(amt("payment_pending"))} · ${share(count("payment_pending"), delivered)} of delivered`,
      description: "No courier-reported remittance yet",
      drill: "payment_pending",
      accent: "red",
    },
  ]
}

function prepaidCards(s: RemittanceSummary): KpiCard[] {
  const paid = Number(s.denominators.paid_orders)
  const gross = String(s.denominators.gross_collection)
  const expectedNet = String(s.denominators.expected_net)
  const count = (k: string) => Number(num(s, k, "count"))
  const amt = (k: string) => String(num(s, k, "amount"))
  return [
    {
      key: "gross",
      label: "Total Prepaid Collected",
      value: rupees(gross),
      detail: `${count("gross_collection")} paid orders`,
      description: "Captured payments recorded for these orders",
      drill: "gross",
      accent: "violet",
    },
    {
      key: "received",
      label: "Total Settlement Received",
      value: rupees(amt("reported_settlement")),
      detail: `${count("reported_settlement")} orders · Prepaid bank confirmed ${rupees(amt("bank_settlement"))}`,
      description: "Successful net settlement reported by the gateway. Not bank proof",
      drill: "reported_settlement",
      accent: "green",
    },
    {
      key: "pending",
      label: "Settlement Pending",
      value: rupees(amt("pending")),
      detail: `${count("pending")} orders · ${share(count("pending"), paid)} of paid orders`,
      description: "Paid, with no successful gateway-reported settlement yet",
      drill: "pending",
      accent: "amber",
    },
    {
      key: "charges",
      label: "Gateway Charges",
      value: rupees(amt("gateway_charges")),
      detail: `${count("gateway_charges")} orders · ${share(amt("gateway_charges"), gross)} of gross`,
      description: "From Cashfree settlements / uploaded reports",
      drill: "charges",
      accent: "orange",
    },
    {
      key: "gst",
      label: "GST",
      value: rupees(amt("gst")),
      detail: `${count("gst")} orders · ${share(amt("gst"), gross)} of gross`,
      description: "GST on gateway charges",
      drill: "gst",
      accent: "orange",
    },
    {
      key: "refunds",
      label: "Refunds",
      value: rupees(amt("refunds")),
      detail: `${count("refunds")} orders · ${share(amt("refunds"), gross)} of gross`,
      description: "Completed refunds on these orders",
      drill: "refunds",
      accent: "red",
    },
    {
      key: "difference",
      label: "Settlement Difference",
      value: rupees(amt("settlement_difference")),
      detail: `${count("settlement_difference")} orders · ${share(Math.abs(Number(amt("settlement_difference"))), expectedNet)} of expected`,
      description: "Expected net − Total Settlement Received",
      drill: "difference",
      accent: "amber",
    },
    {
      key: "unmatched",
      label: "Unmatched Transactions",
      value: String(count("unmatched")),
      detail: `${rupees(amt("unmatched"))} gateway-reported`,
      description: "Settlement lines with no prepaid order",
      drill: "unmatched",
      accent: "orange",
    },
    {
      key: "reconciled",
      label: "Reconciled Transactions",
      value: String(count("reconciled")),
      detail: `${rupees(amt("reconciled"))} expected net`,
      description: "Settlement received equals expected net and is bank-confirmed",
      drill: "reconciled",
      accent: "green",
    },
    {
      key: "bank",
      label: "Prepaid Bank Confirmed",
      value: rupees(amt("bank_settlement")),
      detail: `${count("bank_settlement")} orders · ${share(amt("bank_settlement"), amt("reported_settlement"))} of settlement received`,
      description: `Actual bank credits: ${evidenceSplit(s, "bank_settlement")}`,
      drill: "bank_settlement",
      accent: "green",
      supporting: true,
    },
    unmatchedCreditsCard(s),
    {
      key: "orders",
      label: "Total Prepaid Orders",
      value: Number(s.denominators.prepaid_orders).toLocaleString("en-IN"),
      detail: `${paid} with a captured payment`,
      description: "All prepaid orders in the period",
      drill: "orders",
      accent: "blue",
      supporting: true,
    },
    {
      key: "expected",
      label: "Expected Net Settlement",
      value: rupees(expectedNet),
      detail: `${count("expected_net")} paid orders`,
      description: "Gross − charges − GST − refunds",
      drill: "expected_net",
      accent: "blue",
      supporting: true,
    },
    completionCard(s, "Fully reconciled ÷ paid prepaid orders"),
  ]
}

const STATUS_TITLE = (status: RemittanceStatus) => `${STATUS_STYLE[status].label} orders`

function StatusBadge({ status }: { status: RemittanceStatus }) {
  const style = STATUS_STYLE[status]
  return (
    <Badge
      variant="outline"
      className={cn("rounded-md border-transparent font-semibold", style.className)}
    >
      {style.label}
    </Badge>
  )
}

function dateOrDash(value: string | null | undefined) {
  return value ? formatDate(value) : "—"
}

interface Props {
  module: RemittanceModule
}

/** Modules 6 & 7 dashboard. One implementation for both so every KPI,
 * percentage, chart element and table row behaves identically: clicking it
 * opens the exact records behind it, under the same filters.
 */
export function RemittanceDashboard({ module }: Props) {
  const { hasPermission } = useAuth()
  const canUpload = hasPermission("reconciliation.upload")
  const canAdjust = hasPermission("reconciliation.adjust")
  const isCod = module === "cod"
  const partnerKey: "partner" | "gateway" = isCod ? "partner" : "gateway"

  const { range, setRange, dateFrom, dateTo, isDefaultRange } = useDateRangeFilter()
  const [partner, setPartner] = React.useState<string | undefined>(undefined)
  const [status, setStatus] = React.useState<string | undefined>(undefined)
  const [selection, setSelection] = React.useState<Selection | null>(null)
  const [search, setSearch] = React.useState("")
  const debouncedSearch = useDebouncedValue(search)
  const [upload, setUpload] = React.useState<UploadKind | null>(null)
  const [matching, setMatching] = React.useState<UnmatchedRow | null>(null)
  const [adjusting, setAdjusting] = React.useState<CodRecord | PrepaidRecord | null>(null)
  const [viewing, setViewing] = React.useState<CodRecord | PrepaidRecord | null>(null)
  const [viewingUnmatched, setViewingUnmatched] = React.useState<UnmatchedRow | null>(
    null
  )
  const [exporting, setExporting] = React.useState(false)
  const drilldownRef = React.useRef<HTMLDivElement>(null)
  const { page, pageSize, setPage, resetPage } = usePaginationState()

  const filters: RemittanceFilters = {
    date_from: dateFrom,
    date_to: dateTo,
    [partnerKey]: partner,
    status,
  }

  const summaryQuery = useRemittanceSummary(module, filters)
  const optionsQuery = useRemittanceReport<PartnerRow & GatewayRow>(
    module,
    isCod ? "partners" : "gateways",
    { date_from: dateFrom, date_to: dateTo }
  )
  const options = Array.from(
    new Set(
      (optionsQuery.data ?? [])
        .map((row) => (isCod ? row.partner : row.gateway))
        .filter(Boolean)
    )
  )
  const rematch = useRematch(module)

  // The drilldown's own filters: the page filters plus whatever was clicked.
  const drillFilters: RemittanceFilters = { ...filters, q: debouncedSearch || undefined }
  if (selection?.type === "partner") drillFilters[partnerKey] = selection.key
  if (selection?.type === "status") drillFilters.status = selection.key
  if (selection?.type === "unmatched" && selection.partner) {
    drillFilters[partnerKey] = selection.partner
  }

  const recordsSelected = selection !== null && selection.type !== "unmatched"
  const recordsQuery = useRemittanceRecords<CodRecord | PrepaidRecord>({
    module,
    kpi:
      selection?.type === "kpi"
        ? selection.key
        : selection?.type === "partner"
          ? selection.kpi
          : undefined,
    ageBucket: selection?.type === "age" ? selection.bucket : undefined,
    settledOn: selection?.type === "settled" ? selection.day : undefined,
    filters: drillFilters,
    page,
    pageSize,
    enabled: recordsSelected,
  })
  const unmatchedQuery = useRemittanceUnmatched(
    module,
    drillFilters,
    selection?.type === "unmatched" ? selection.kind : undefined,
    page,
    pageSize,
    selection?.type === "unmatched"
  )

  function select(next: Selection) {
    const same = JSON.stringify(selection) === JSON.stringify(next)
    setSelection(same ? null : next)
    setSearch("")
    resetPage()
    if (!same) {
      window.setTimeout(
        () =>
          drilldownRef.current?.scrollIntoView?.({ behavior: "smooth", block: "start" }),
        50
      )
    }
  }

  function selectKpi(drill: string, title: string) {
    const kind = unmatchedKindOf(module, drill)
    if (kind) {
      select({ type: "unmatched", title, kind, card: drill })
    } else {
      select({ type: "kpi", key: drill, title })
    }
  }

  function clear() {
    setSelection(null)
    setSearch("")
    resetPage()
  }

  function changeFilter(update: () => void) {
    update()
    resetPage()
  }

  const unmatchedColumns: DataTableColumn<UnmatchedRow>[] = [
    {
      id: "kind",
      header: "Type",
      cell: (row) => (
        <Badge variant="outline">
          {row.kind === "bank_credit"
            ? "Bank credit"
            : row.kind === "gateway_line"
              ? "Settlement line"
              : "Remittance line"}
        </Badge>
      ),
    },
    {
      id: "contribution",
      header: "In total",
      className: "text-right",
      cell: (r) => (
        <span className="font-semibold">
          {r.contribution === undefined ? "—" : rupees(r.contribution)}
        </span>
      ),
    },
    {
      id: "source",
      header: isCod ? "Partner" : "Gateway",
      cell: (r) => r.partner ?? r.gateway ?? "—",
    },
    {
      id: "ref1",
      header: isCod ? "AWB" : "Transaction ID",
      cell: (r) => r.awb ?? r.transaction_id ?? "—",
    },
    { id: "ref2", header: "Order reference", cell: (r) => r.order_ref ?? "—" },
    ...(isCod
      ? []
      : [
          {
            id: "cfid",
            header: "Cashfree payment ID",
            cell: (r: UnmatchedRow) => r.cashfree_payment_id ?? "—",
          },
          {
            id: "settlement",
            header: "Settlement ID",
            cell: (r: UnmatchedRow) => r.settlement_id ?? "—",
          },
        ]),
    { id: "reference", header: "Reference / UTR", cell: (r) => r.reference ?? "—" },
    ...(isCod
      ? []
      : (
          [
            ["gross", "Gross", "gross_amount"],
            ["charges", "Charges", "gateway_charges"],
            ["gst", "GST", "gst_amount"],
            ["refund", "Refunds", "refund_amount"],
          ] as const
        ).map(([id, header, field]) => ({
          id,
          header,
          className: "text-right",
          cell: (r: UnmatchedRow) => {
            const value = r[field]
            return value === null || value === undefined ? "—" : rupees(value)
          },
        }))),
    {
      id: "amount",
      header: "Amount",
      className: "text-right",
      cell: (r) => rupees(r.amount),
    },
    { id: "date", header: "Date", cell: (r) => dateOrDash(r.date) },
    {
      id: "reason",
      header: "Why unmatched",
      cell: (r) => <span className="text-muted-foreground text-xs">{r.reason}</span>,
    },
    // Manual match: COD remittance lines only, and only for users who may adjust.
    ...(isCod && canAdjust
      ? [
          {
            id: "match",
            header: "",
            cell: (r: UnmatchedRow) =>
              r.kind === "remittance_line" ? (
                <Button
                  size="sm"
                  variant="outline"
                  onClick={(event) => {
                    event.stopPropagation()
                    setMatching(r)
                  }}
                >
                  Match to order
                </Button>
              ) : null,
          },
        ]
      : []),
  ]

  const drillTitle = (() => {
    if (!selection) return ""
    switch (selection.type) {
      case "kpi":
        return selection.title
      case "status":
        return STATUS_TITLE(selection.key)
      case "partner":
        return `${selection.title ?? selection.key}${selection.kpi ? ` — ${selection.kpi.replaceAll("_", " ")}` : ""}`
      case "unmatched":
        return selection.title
      case "age":
        return `Outstanding ${selection.bucket}`
      case "settled":
        return `Settled on ${formatDate(selection.day)}`
    }
  })()

  // The clicked card's own figure, so the drilldown can prove it reconciles.
  const cardDrill =
    selection?.type === "kpi"
      ? selection.key
      : selection?.type === "unmatched"
        ? selection.card
        : undefined
  const cardKpi = cardDrill
    ? summaryQuery.data?.kpis[KPI_OF_DRILL[module][cardDrill] ?? ""]
    : undefined

  // The meaning of the open figure, visible in the drilldown (no hover needed).
  const definitionDrill =
    cardDrill ?? (selection?.type === "partner" ? selection.kpi : undefined)
  const definition = definitionDrill ? definitionOf(module, definitionDrill) : undefined

  async function exportDrilldown() {
    if (!selection) return
    setExporting(true)
    try {
      await downloadDrilldownExport(module, {
        view: selection.type === "unmatched" ? "unmatched" : "records",
        filters: drillFilters,
        kpi:
          selection.type === "kpi"
            ? selection.key
            : selection.type === "partner"
              ? selection.kpi
              : undefined,
        kind: selection.type === "unmatched" ? selection.kind : undefined,
        ageBucket: selection.type === "age" ? selection.bucket : undefined,
        settledOn: selection.type === "settled" ? selection.day : undefined,
      })
    } catch (error) {
      toast.error(getApiErrorMessage(error))
    } finally {
      setExporting(false)
    }
  }

  function drilldownHeader(
    query: typeof recordsQuery | typeof unmatchedQuery,
    unmatchedView: boolean
  ) {
    const pageRows: { contribution?: string }[] = query.data?.data ?? []
    return (
      <>
        <DrilldownFilters
          range={range}
          onRangeChange={(value) => changeFilter(() => setRange(value))}
          status={status}
          onStatusChange={(value) => changeFilter(() => setStatus(value))}
          statuses={isCod ? COD_STATUSES : PREPAID_STATUSES}
          showStatus={!unmatchedView}
          onExport={() => void exportDrilldown()}
          exporting={exporting}
        />
        <DrilldownTotals
          totals={query.data?.totals}
          card={cardKpi}
          definition={definition}
          searching={Boolean(debouncedSearch)}
          pageAmount={
            pageRows.reduce(
              (sum, row) => sum + Math.round(Number(row.contribution ?? 0) * 100),
              0
            ) / 100
          }
        />
      </>
    )
  }

  const contributionColumn: DataTableColumn<CodRecord | PrepaidRecord> = {
    id: "contribution",
    header: "In total",
    className: "text-right",
    cell: (row) => (
      <span className="font-semibold">
        {row.contribution === undefined ? "—" : rupees(row.contribution)}
      </span>
    ),
  }

  const [orderColumn, ...otherColumns] = isCod
    ? codColumns(canAdjust, setAdjusting)
    : prepaidColumns(canAdjust, setAdjusting)
  const recordColumns = [orderColumn, contributionColumn, ...otherColumns]

  async function exportExcel() {
    try {
      await downloadRemittanceExport(module, filters)
    } catch (error) {
      toast.error(getApiErrorMessage(error))
    }
  }

  async function runRematch() {
    try {
      const result = await rematch.mutateAsync()
      toast.success(
        `Matching re-run: ${result.matched ?? 0} matched, ${result.still_unmatched ?? 0} still unmatched.`
      )
    } catch (error) {
      toast.error(getApiErrorMessage(error))
    }
  }

  const statuses = isCod ? COD_STATUSES : PREPAID_STATUSES

  return (
    <div className="space-y-5">
      <PageHeader
        title={
          isCod ? "COD Remittance Reconciliation" : "Prepaid Remittance Reconciliation"
        }
        description={
          isCod
            ? "Delivered COD orders against what shipping partners remitted and what reached the bank. Click any number, percentage, chart or row to see the records behind it."
            : "Prepaid collections against gateway settlements and bank credits. Click any number, percentage, chart or row to see the records behind it."
        }
      />
      <ReconciliationTabs />

      <FilterBar
        dateRangeIsDefault={isDefaultRange}
        dateRange={range}
        onDateRangeChange={(value) => changeFilter(() => setRange(value))}
        statusValue={status}
        onStatusChange={(value) => changeFilter(() => setStatus(value))}
        statusOptions={statuses.map((s) => ({ label: STATUS_STYLE[s].label, value: s }))}
        statusLabel="Status"
        extra={
          <Select
            value={partner ?? ALL}
            onValueChange={(value) =>
              changeFilter(() => setPartner(value === ALL ? undefined : value))
            }
          >
            <SelectTrigger
              className="w-[190px]"
              aria-label={isCod ? "Shipping partner" : "Payment gateway"}
            >
              <SelectValue placeholder={isCod ? "Shipping partner" : "Payment gateway"} />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL}>
                {isCod ? "All shipping partners" : "All gateways"}
              </SelectItem>
              {options.map((name) => (
                <SelectItem key={name} value={name}>
                  {name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        }
      />

      {!isCod && <CashfreeSyncPanel canSync={canUpload} />}

      <div className="flex flex-wrap gap-2">
        {canUpload && (
          <>
            <Button
              size="sm"
              variant={isCod ? "default" : "outline"}
              onClick={() => setUpload("report")}
            >
              <FileUp className="size-4" />
              {isCod ? "Upload remittance report" : "Upload settlement report (fallback)"}
            </Button>
            <Button size="sm" variant="outline" onClick={() => setUpload("bank")}>
              <Landmark className="size-4" />
              Upload bank statement
            </Button>
          </>
        )}
        {canAdjust && (
          <Button
            size="sm"
            variant="outline"
            onClick={() => void runRematch()}
            disabled={rematch.isPending}
          >
            <RefreshCw className="size-4" />
            Re-run matching
          </Button>
        )}
        <Button size="sm" variant="outline" onClick={() => void exportExcel()}>
          <Download className="size-4" />
          Export to Excel
        </Button>
      </div>

      <QueryStates
        isLoading={summaryQuery.isLoading}
        isError={summaryQuery.isError}
        error={summaryQuery.error}
        data={summaryQuery.data}
        onRetry={() => summaryQuery.refetch()}
      >
        {(summary) => {
          const cards = isCod ? codCards(summary) : prepaidCards(summary)
          const rows = (isCod ? summary.partners : summary.gateways) ?? []
          const isSelectedCard = (card: KpiCard) =>
            (selection?.type === "kpi" &&
              selection.key === card.drill &&
              selection.title === card.label) ||
            (selection?.type === "unmatched" && selection.card === card.drill)
          return (
            <>
              {isCod && (
                <section className="space-y-2" aria-label="Payment status">
                  <h3 className="text-base font-semibold">Payment status</h3>
                  <div
                    className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-5"
                    data-testid="payment-status-kpi-grid"
                  >
                    <Card className="h-full border-l-4 border-l-slate-400">
                      <CardContent className="space-y-1">
                        <p className="text-muted-foreground text-sm font-medium">
                          Total COD Orders
                        </p>
                        <p className="text-3xl font-semibold tabular-nums">
                          {Number(summary.denominators.total_cod_orders ?? 0).toLocaleString(
                            "en-IN"
                          )}
                        </p>
                        <p className="text-foreground/80 text-xs font-medium">
                          {rupees(String(summary.denominators.total_cod_amount ?? "0"))} total
                          COD value
                        </p>
                        <p className="text-muted-foreground text-xs">
                          Every non-cancelled COD order in the period, delivered or not -- not
                          clickable, since that is a wider population than this page&apos;s
                          delivered-orders drilldowns.
                        </p>
                      </CardContent>
                    </Card>
                    {paymentStatusCards(summary).map((card) => (
                      <SelectableStatCard
                        key={card.key}
                        label={card.label}
                        value={card.value}
                        detail={card.detail}
                        description={card.description}
                        accent={card.accent}
                        tooltip={definitionOf(module, card.drill)}
                        selected={isSelectedCard(card)}
                        onClick={() => selectKpi(card.drill, card.label)}
                      />
                    ))}
                  </div>
                </section>
              )}
              {[false, true].map((supporting) => (
                <section
                  key={String(supporting)}
                  className="space-y-2"
                  aria-label={supporting ? "Supporting figures" : "Key figures"}
                >
                  {supporting && (
                    <h3 className="text-base font-semibold">Supporting figures</h3>
                  )}
                  <div
                    className={cn(
                      "grid grid-cols-1 gap-3 sm:grid-cols-2",
                      isCod ? "xl:grid-cols-4" : "xl:grid-cols-5"
                    )}
                    data-testid={supporting ? "kpi-grid-supporting" : "kpi-grid"}
                  >
                    {cards
                      .filter((card) => Boolean(card.supporting) === supporting)
                      .map((card) => (
                        <SelectableStatCard
                          key={card.key}
                          label={card.label}
                          value={card.value}
                          detail={card.detail}
                          description={card.description}
                          accent={card.accent}
                          tooltip={definitionOf(module, card.drill)}
                          selected={isSelectedCard(card)}
                          onClick={() => selectKpi(card.drill, card.label)}
                        />
                      ))}
                  </div>
                </section>
              ))}

              <BankPositionPanel
                module={module}
                summary={summary}
                selectedTitle={selection?.type === "kpi" ? selection.title : undefined}
                onSelect={selectKpi}
              />

              <section className="space-y-2" aria-label="Percentages">
                <div>
                  <h3 className="text-base font-semibold">Key percentages</h3>
                  <p className="text-muted-foreground text-xs">
                    Each percentage shows what it is a share of. “By count” compares
                    numbers of orders or transactions; “by value” compares rupee amounts.
                    Click one to see the numerator records.
                  </p>
                </div>
                <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 xl:grid-cols-3">
                  {summary.percentages.map((item) => (
                    <button
                      key={item.key}
                      type="button"
                      aria-pressed={
                        item.drill === "unmatched"
                          ? selection?.type === "unmatched" &&
                            selection.card === "unmatched"
                          : selection?.type === "kpi" && selection.key === item.drill
                      }
                      onClick={() => item.drill && selectKpi(item.drill, item.label)}
                      title={item.drill ? definitionOf(module, item.drill) : undefined}
                      className={cn(
                        "hover:border-primary/50 bg-card flex items-start justify-between gap-3 rounded-lg border p-3 text-left transition-colors",
                        ((item.drill === "unmatched" &&
                          selection?.type === "unmatched" &&
                          selection.card === "unmatched") ||
                          (selection?.type === "kpi" && selection.key === item.drill)) &&
                          "border-primary bg-primary/5"
                      )}
                    >
                      <span className="min-w-0">
                        <span className="block text-sm font-medium">{item.label}</span>
                        <span className="text-muted-foreground block text-xs">
                          {item.value === null ? "Not available — " : ""}
                          {percentageBasis(item)}
                        </span>
                      </span>
                      <span className="shrink-0 text-right">
                        <span className="block text-xl font-semibold tabular-nums">
                          {percentageText(item)}
                        </span>
                        <Badge variant="outline" className="text-[10px]">
                          by {item.kind}
                        </Badge>
                      </span>
                    </button>
                  ))}
                </div>
              </section>

              <div className="grid gap-4 xl:grid-cols-2">
                <section className="bg-card min-w-0 space-y-3 rounded-xl border p-4">
                  <div>
                    <h3 className="text-base font-semibold">Reconciliation status</h3>
                    <p className="text-muted-foreground text-xs">
                      Every order is in exactly one status. Click a section to list those
                      orders.
                    </p>
                  </div>
                  <ClickableDonut
                    ariaLabel="Orders by reconciliation status"
                    centerLabel="orders"
                    selectedKey={selection?.type === "status" ? selection.key : null}
                    onSelect={(key) =>
                      select({ type: "status", key: key as RemittanceStatus })
                    }
                    data={summary.status_breakdown.map((s) => ({
                      key: s.status,
                      label: s.label,
                      value: s.count,
                      color: STATUS_STYLE[s.status].color,
                    }))}
                  />
                </section>
                <section className="bg-card min-w-0 space-y-3 rounded-xl border p-4">
                  <div>
                    <h3 className="text-base font-semibold">
                      {isCod
                        ? "Delivered orders by shipping partner"
                        : "Prepaid orders by gateway"}
                    </h3>
                    <p className="text-muted-foreground text-xs">
                      Click a bar to list that {isCod ? "partner" : "gateway"}’s orders.
                    </p>
                  </div>
                  {rows.length === 0 ? (
                    <p className="text-muted-foreground text-sm">
                      No data in the selected range.
                    </p>
                  ) : (
                    <ClickableBarChart
                      ariaLabel={
                        isCod ? "Delivered orders by partner" : "Orders by gateway"
                      }
                      data={rows.map((row) => {
                        const name = isCod
                          ? (row as PartnerRow).partner
                          : (row as GatewayRow).gateway
                        return {
                          key: name,
                          label: name,
                          value: isCod
                            ? (row as PartnerRow).delivered_orders
                            : (row as GatewayRow).orders,
                          color: "#2563eb",
                        }
                      })}
                      selectedKey={selection?.type === "partner" ? selection.key : null}
                      onSelect={(key) => select({ type: "partner", key, title: key })}
                      valueLabel="Orders"
                    />
                  )}
                </section>
              </div>
            </>
          )
        }}
      </QueryStates>

      <div ref={drilldownRef}>
        {selection && selection.type !== "unmatched" && (
          <DataDrilldown
            title={drillTitle}
            description="Same date range, status and partner filters as the numbers above. Click a record for its full details."
            query={recordsQuery}
            columns={recordColumns}
            rowKey={(row) => row.order_id}
            onRowClick={setViewing}
            header={drilldownHeader(recordsQuery, false)}
            search={search}
            onSearchChange={(value) => {
              setSearch(value)
              resetPage()
            }}
            onPageChange={setPage}
            onClear={clear}
            searchPlaceholder={
              isCod
                ? "Search order, AWB or remittance reference"
                : "Search order, gateway transaction ID or bank reference"
            }
          />
        )}
        {selection?.type === "unmatched" && (
          <DataDrilldown
            title={drillTitle}
            description="Nothing here has been discarded: unmatched items stay visible until matched. Reported amounts and bank credits are listed separately."
            query={unmatchedQuery}
            columns={unmatchedColumns}
            rowKey={(row) => `${row.kind}-${row.id}`}
            onRowClick={setViewingUnmatched}
            header={drilldownHeader(unmatchedQuery, true)}
            search={search}
            onSearchChange={(value) => {
              setSearch(value)
              resetPage()
            }}
            onPageChange={setPage}
            onClear={clear}
            searchPlaceholder="Search reference, AWB / transaction ID or order reference"
          />
        )}
      </div>

      <RemittanceReports
        module={module}
        filters={filters}
        selection={selection}
        onSelect={select}
        canUpload={canUpload}
        canAdjust={canAdjust}
        onUpload={setUpload}
        onMatchLine={isCod && canAdjust ? setMatching : undefined}
      />

      {viewing && (
        <RecordDetailDialog
          module={module}
          record={viewing}
          onClose={() => setViewing(null)}
        />
      )}
      {viewingUnmatched && (
        <UnmatchedDetailDialog
          row={viewingUnmatched}
          isCod={isCod}
          onClose={() => setViewingUnmatched(null)}
        />
      )}
      {upload && (
        <UploadDialog
          module={module}
          kind={upload}
          open
          onOpenChange={(open) => !open && setUpload(null)}
          suggestions={options}
        />
      )}
      {matching && (
        <ManualMatchDialog
          line={matching}
          open
          onOpenChange={(open) => !open && setMatching(null)}
        />
      )}
      {adjusting && (
        <AdjustDialog
          module={module}
          record={adjusting}
          open
          onOpenChange={(open) => !open && setAdjusting(null)}
        />
      )}
    </div>
  )
}

function TwoLine({ top, bottom }: { top?: string | null; bottom?: string | null }) {
  return (
    <span className="capitalize">
      {top?.replaceAll("_", " ") ?? "—"}
      <span className="text-muted-foreground block text-xs">
        {bottom?.replaceAll("_", " ") ?? "—"}
      </span>
    </span>
  )
}

type AdjustHandler = (record: CodRecord | PrepaidRecord) => void

function adjustColumn(
  canAdjust: boolean,
  onAdjust: AdjustHandler
): DataTableColumn<CodRecord | PrepaidRecord>[] {
  if (!canAdjust) return []
  return [
    {
      id: "adjust",
      header: "",
      cell: (row) => (
        <Button
          size="sm"
          variant="ghost"
          onClick={(event) => {
            event.stopPropagation()
            onAdjust(row)
          }}
        >
          Adjust
        </Button>
      ),
    },
  ]
}

const BANK_BADGE: Record<string, string> = {
  confirmed: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-400",
  partially_confirmed: "bg-amber-500/15 text-amber-700 dark:text-amber-400",
}

function bankColumns(): DataTableColumn<CodRecord | PrepaidRecord>[] {
  return [
    {
      id: "bank-status",
      header: "Bank confirmation",
      cell: (row) => (
        <span className="text-xs">
          <Badge
            variant="outline"
            className={cn(
              "rounded-md border-transparent font-semibold",
              BANK_BADGE[row.bank_status] ?? "bg-muted text-muted-foreground"
            )}
          >
            {row.bank_status_label}
          </Badge>
          <span className="text-muted-foreground mt-0.5 block">
            {row.bank_evidence ?? "No bank evidence"}
          </span>
        </span>
      ),
    },
    {
      id: "bank-remaining",
      header: "Remaining amount",
      className: "text-right",
      cell: (row) => (
        <span>
          {rupees(row.bank_remaining)}
          <span className="text-muted-foreground block text-xs">
            of {rupees(row.bank_expected)} expected
          </span>
        </span>
      ),
    },
    {
      id: "adjustment",
      header: "Manual adjustment",
      className: "text-right",
      cell: (row) =>
        Number(row.adjustment_amount) === 0 ? (
          "—"
        ) : (
          <span title={row.adjustment_effect}>
            {signedRupees(row.adjustment_amount)}
            <span className="text-muted-foreground block text-xs">
              difference before: {signedRupees(row.difference_before_adjustments)}
            </span>
          </span>
        ),
    },
  ]
}

function codColumns(
  canAdjust: boolean,
  onAdjust: AdjustHandler
): DataTableColumn<CodRecord | PrepaidRecord>[] {
  const c = (row: CodRecord | PrepaidRecord) => row as CodRecord
  return [
    {
      id: "order",
      header: "Order",
      cell: (row) => (
        <span className="font-medium">
          {c(row).order_number}
          <span className="text-muted-foreground block text-xs font-normal">
            {formatDate(c(row).order_datetime)}
          </span>
        </span>
      ),
    },
    { id: "awb", header: "AWB", cell: (row) => c(row).awb ?? "—" },
    {
      id: "statuses",
      header: "Payment / delivery",
      cell: (row) => (
        <TwoLine top={c(row).payment_status} bottom={c(row).delivery_status} />
      ),
    },
    { id: "customer", header: "Customer", cell: (row) => c(row).customer_name ?? "—" },
    {
      id: "partner",
      header: "Shipping partner",
      cell: (row) => (
        <span>
          {c(row).partner}
          {c(row).service_name && c(row).service_name !== c(row).partner && (
            <span className="text-muted-foreground block text-xs">
              {c(row).service_name}
            </span>
          )}
        </span>
      ),
    },
    {
      id: "delivery",
      header: "Delivered",
      cell: (row) => dateOrDash(c(row).delivery_date),
    },
    {
      id: "expected",
      header: "COD amount",
      className: "text-right",
      cell: (row) => rupees(c(row).expected_amount),
    },
    {
      id: "remitted",
      header: "COD settled (courier)",
      className: "text-right",
      cell: (row) => rupees(c(row).remittance_amount),
    },
    {
      id: "received",
      header: "COD bank confirmed",
      className: "text-right",
      cell: (row) => rupees(c(row).amount_received),
    },
    {
      id: "difference",
      header: "Difference",
      className: "text-right",
      cell: (row) => (
        <span
          className={cn(
            Number(c(row).difference) < 0 && "text-red-600",
            Number(c(row).difference) > 0 && "text-purple-600"
          )}
        >
          {signedRupees(c(row).difference)}
        </span>
      ),
    },
    {
      id: "reference",
      header: "Remittance ref / date",
      cell: (row) => (
        <span className="text-xs">
          {c(row).remittance_reference ?? "—"}
          <span className="text-muted-foreground block">
            {dateOrDash(c(row).remittance_date)}
          </span>
        </span>
      ),
    },
    {
      id: "bankdate",
      header: "Bank credit date",
      cell: (row) => dateOrDash(c(row).bank_credit_date),
    },
    ...bankColumns(),
    {
      id: "status",
      header: "Status",
      cell: (row) => <StatusBadge status={c(row).status} />,
    },
    ...adjustColumn(canAdjust, onAdjust),
  ]
}

function prepaidColumns(
  canAdjust: boolean,
  onAdjust: AdjustHandler
): DataTableColumn<CodRecord | PrepaidRecord>[] {
  const p = (row: CodRecord | PrepaidRecord) => row as PrepaidRecord
  return [
    {
      id: "order",
      header: "Order",
      cell: (row) => (
        <span className="font-medium">
          {p(row).order_number}
          <span className="text-muted-foreground block text-xs font-normal">
            {formatDate(p(row).order_datetime)}
          </span>
        </span>
      ),
    },
    { id: "customer", header: "Customer", cell: (row) => p(row).customer_name ?? "—" },
    {
      id: "statuses",
      header: "Payment / fulfilment",
      cell: (row) => (
        <TwoLine top={p(row).payment_status} bottom={p(row).delivery_status} />
      ),
    },
    {
      id: "gateway",
      header: "Gateway / transaction ID",
      cell: (row) => (
        <span>
          {p(row).gateway}
          <span className="text-muted-foreground block text-xs">
            {p(row).gateway_transaction_id ?? "—"}
          </span>
        </span>
      ),
    },
    {
      id: "cfid",
      header: "Cashfree payment ID",
      cell: (row) => p(row).cashfree_payment_id ?? "—",
    },
    {
      id: "settlement",
      header: "Settlement ID",
      cell: (row) => p(row).settlement_id ?? "—",
    },
    {
      id: "txdate",
      header: "Transaction date",
      cell: (row) => dateOrDash(p(row).transaction_date),
    },
    {
      id: "gross",
      header: "Gross",
      className: "text-right",
      cell: (row) => rupees(p(row).gross_amount),
    },
    {
      id: "charges",
      header: "Charges",
      className: "text-right",
      cell: (row) => rupees(p(row).gateway_charges),
    },
    {
      id: "gst",
      header: "GST",
      className: "text-right",
      cell: (row) => rupees(p(row).gst_amount),
    },
    {
      id: "refund",
      header: "Refund",
      className: "text-right",
      cell: (row) => rupees(p(row).refund_amount),
    },
    {
      id: "expected",
      header: "Expected net",
      className: "text-right",
      cell: (row) => rupees(p(row).expected_net),
    },
    {
      id: "actual",
      header: "Settlement received (gateway)",
      className: "text-right",
      cell: (row) => rupees(p(row).actual_settlement),
    },
    {
      id: "confirmed",
      header: "Prepaid bank confirmed",
      className: "text-right",
      cell: (row) => rupees(p(row).bank_confirmed_settlement),
    },
    {
      id: "bank",
      header: "UTR / reference",
      cell: (row) => (
        <span className="text-xs">
          {p(row).bank_reference ?? "—"}
          <span className="text-muted-foreground block">
            Settled {dateOrDash(p(row).settlement_date)}
          </span>
          <span className="text-muted-foreground block">
            Bank credit {dateOrDash(p(row).bank_credit_date)}
          </span>
        </span>
      ),
    },
    {
      id: "difference",
      header: "Status difference",
      className: "text-right",
      cell: (row) => (
        <span
          className={cn(
            Number(p(row).difference) < 0 && "text-red-600",
            Number(p(row).difference) > 0 && "text-purple-600"
          )}
        >
          {signedRupees(p(row).difference)}
        </span>
      ),
    },
    ...bankColumns(),
    {
      id: "unverified",
      header: "Unverified (not counted)",
      className: "text-right",
      cell: (row) =>
        p(row).unverified_line_count ? (
          <span className="text-amber-700 dark:text-amber-400">
            {rupees(p(row).unverified_amount ?? "0")}
            <span className="block text-xs">status: {p(row).unverified_statuses}</span>
          </span>
        ) : (
          "—"
        ),
    },
    {
      id: "status",
      header: "Status",
      cell: (row) => <StatusBadge status={p(row).status} />,
    },
    ...adjustColumn(canAdjust, onAdjust),
  ]
}

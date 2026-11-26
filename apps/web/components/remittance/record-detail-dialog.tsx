"use client"

import * as React from "react"
import Link from "next/link"
import { ExternalLink } from "lucide-react"

import { QueryStates } from "@/components/shared/query-states"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { formatDate, formatDateTime } from "@/lib/format"
import { rupees, signedRupees } from "@/lib/remittance-format"
import { useReconciliationDetail } from "@/services/remittance"
import type {
  BankPosition,
  CodRecord,
  PrepaidRecord,
  ReconciliationDetail,
  RemittanceModule,
  UnmatchedRow,
} from "@/types/remittance"

type Value = string | number | null | undefined

const text = (value: Value) =>
  value === null || value === undefined || value === "" ? "—" : String(value)
const words = (value: Value) => text(value).replaceAll("_", " ")
const money = (value: Value) =>
  value === null || value === undefined ? "—" : rupees(String(value))
const day = (value: string | null | undefined) => (value ? formatDate(value) : "—")

function Fields({ items }: { items: [string, React.ReactNode][] }) {
  return (
    <dl className="grid grid-cols-1 gap-x-6 gap-y-1.5 sm:grid-cols-2">
      {items.map(([label, value]) => (
        <div key={label} className="flex justify-between gap-3 border-b pb-1">
          <dt className="text-muted-foreground shrink-0">{label}</dt>
          <dd className="min-w-0 text-right font-medium break-all tabular-nums">
            {value}
          </dd>
        </div>
      ))}
    </dl>
  )
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="space-y-2">
      <h4 className="font-semibold">{title}</h4>
      {children}
    </section>
  )
}

const Empty = ({ children }: { children: React.ReactNode }) => (
  <p className="text-muted-foreground text-xs">{children}</p>
)

function codFields(r: CodRecord): [string, React.ReactNode][] {
  return [
    ["Customer", text(r.customer_name)],
    ["Order date", day(r.order_datetime)],
    ["Payment status", words(r.payment_status)],
    ["Delivery status", words(r.delivery_status)],
    ["Delivery date", day(r.delivery_date)],
    ["AWB", text(r.awb)],
    ["Courier partner", text(r.partner)],
    ["Order amount", money(r.order_amount)],
    ["COD amount", money(r.expected_amount)],
    ["COD settled (courier-reported)", money(r.remittance_amount)],
    ["COD bank confirmed", money(r.amount_received)],
    ["Remittance reference", text(r.remittance_reference)],
    ["Settlement date", day(r.remittance_date)],
    ["Bank credit date", day(r.bank_credit_date)],
    ["Reconciliation status", r.status_label],
  ]
}

function prepaidFields(r: PrepaidRecord): [string, React.ReactNode][] {
  return [
    ["Customer", text(r.customer_name)],
    ["Order date", day(r.order_datetime)],
    ["Payment status", words(r.payment_status)],
    ["Fulfilment status", words(r.delivery_status)],
    ["Gateway", r.gateway],
    ["Cashfree payment ID", text(r.cashfree_payment_id)],
    ["Transaction ID", text(r.gateway_transaction_id)],
    ["Settlement ID", text(r.settlement_id)],
    ["UTR / reference", text(r.bank_reference)],
    ["Order amount", money(r.order_amount)],
    ["Gross amount", money(r.gross_amount)],
    ["Charges", money(r.gateway_charges)],
    ["GST", money(r.gst_amount)],
    ["Refunds", money(r.refund_amount)],
    ["Expected net", money(r.expected_net)],
    ["Settlement received (gateway-reported)", money(r.actual_settlement)],
    ["Prepaid bank confirmed", money(r.bank_confirmed_settlement)],
    ["Settlement difference (expected − received)", money(r.settlement_gap)],
    ["Settlement date", day(r.settlement_date)],
    ["Bank credit date", day(r.bank_credit_date)],
    ["Reconciliation status", r.status_label],
  ]
}

function bankFields(r: BankPosition): [string, React.ReactNode][] {
  return [
    ["Bank confirmation", r.bank_status_label],
    ["Total expected", money(r.bank_expected)],
    ["Statement-matched", money(r.bank_statement_amount)],
    ["Manually verified", money(r.bank_manual_amount)],
    ["Remaining amount", money(r.bank_remaining)],
    ...(Number(r.bank_over) > 0
      ? ([["Over-confirmed", money(r.bank_over)]] as [string, React.ReactNode][])
      : []),
  ]
}

function adjustmentFields(
  r: CodRecord | PrepaidRecord,
  isCod: boolean
): [string, React.ReactNode][] {
  return [
    ["Difference before adjustments", signedRupees(r.difference_before_adjustments)],
    ["Manual adjustments (total)", signedRupees(r.adjustment_amount)],
    [
      isCod
        ? "Status difference (settled + adjustments − COD)"
        : "Status difference (received + adjustments − expected)",
      signedRupees(r.difference),
    ],
  ]
}

function DetailBody({ detail }: { detail: ReconciliationDetail }) {
  const isCod = detail.module === "cod"
  return (
    <div className="space-y-4">
      <Section title="Reconciliation">
        <Fields
          items={
            isCod
              ? codFields(detail.record as CodRecord)
              : prepaidFields(detail.record as PrepaidRecord)
          }
        />
      </Section>

      <Section title="Bank confirmation">
        <Fields items={bankFields(detail.record)} />
      </Section>

      <Section title="Effect of manual adjustments">
        <Fields items={adjustmentFields(detail.record, isCod)} />
        <p data-testid="adjustment-effect">{detail.record.adjustment_effect}</p>
      </Section>

      <Section title={isCod ? "Remittance lines" : "Settlement lines"}>
        {detail.lines.length === 0 && (
          <Empty>
            No {isCod ? "uploaded remittance" : "settlement"} line is matched to this
            order.
          </Empty>
        )}
        {detail.lines.map((line) => (
          <div key={line.id} className="space-y-1 rounded-lg border p-3">
            {line.counted === false && (
              <p className="text-xs font-medium text-amber-700 dark:text-amber-400">
                {line.verification === "unverified"
                  ? "Unverified: not counted"
                  : "Not counted as settled"}{" "}
                (status: {line.line_status ? words(line.line_status) : "blank"}).
              </p>
            )}
            <Fields
              items={
                isCod
                  ? [
                      ["Courier partner", text(line.partner)],
                      ["AWB", text(line.awb)],
                      ["Remittance reference", text(line.reference)],
                      ["Remittance amount", money(line.amount)],
                      ["Settlement date", day(line.date)],
                      ["Matched by", words(line.match_method)],
                    ]
                  : [
                      [
                        "Source",
                        line.source === "api" ? "Cashfree API" : "Uploaded file",
                      ],
                      ["Event", words(line.event_type)],
                      ["Transaction ID", text(line.transaction_id)],
                      ["Settlement ID", text(line.settlement_id)],
                      ["UTR / reference", text(line.reference)],
                      ["Gross amount", money(line.gross_amount)],
                      ["Charges", money(line.gateway_charges)],
                      ["GST", money(line.gst_amount)],
                      ["Refunds", money(line.refund_amount)],
                      ["Net amount", money(line.amount)],
                      ["Settlement date", day(line.date)],
                      ["Matched by", words(line.match_method)],
                    ]
              }
            />
          </div>
        ))}
      </Section>

      {!isCod && (
        <Section title="Payments and refunds recorded in the OMS">
          {!detail.payments?.length && !detail.refunds?.length && (
            <Empty>No payment or refund record for this order.</Empty>
          )}
          {detail.payments?.map((p, index) => (
            <p key={`p${index}`}>
              Payment {money(p.amount)} · {words(p.status)} · {text(p.provider)} ·{" "}
              <span className="break-all">{text(p.transaction_id)}</span>
              {p.paid_at ? ` · ${formatDateTime(p.paid_at)}` : ""}
            </p>
          ))}
          {detail.refunds?.map((r, index) => (
            <p key={`r${index}`}>
              Refund {money(r.amount)} · {words(r.status)}
            </p>
          ))}
        </Section>
      )}

      <Section title="Bank evidence">
        {detail.bank_links.length === 0 && (
          <Empty>
            No bank credit is linked yet. A gateway or courier report alone is not proof
            of a bank credit.
          </Empty>
        )}
        {detail.bank_references?.map((ref) => (
          <p key={ref.reference} className="text-xs">
            Reference {ref.reference}: total expected {money(ref.bank_expected)} · bank
            confirmed{" "}
            {money(Number(ref.bank_statement_amount) + Number(ref.bank_manual_amount))} ·
            remaining {money(ref.bank_remaining)} ·{" "}
            <span className="font-semibold">{ref.bank_status_label}</span>
          </p>
        ))}
        {detail.bank_links.map((link, index) => (
          <div key={index} className="space-y-0.5 rounded-lg border p-2">
            <p>
              <Badge
                variant={
                  link.verification === "manually_verified" ? "secondary" : "outline"
                }
              >
                {link.verification_label}
              </Badge>{" "}
              {link.reference}: {money(link.linked_amount)}{" "}
              {link.method === "manual_verification"
                ? "applied from a verified amount of"
                : "linked of a credit of"}{" "}
              {money(link.credit_amount)} on {day(link.credit_date)}
            </p>
            {link.verification === "manually_verified" && (
              <p className="text-muted-foreground text-xs break-all">
                Bank reference {text(link.bank_reference)} · Reason:{" "}
                {link.reason ?? "not recorded (verified before reasons were required)"}
              </p>
            )}
          </div>
        ))}
      </Section>

      <Section title="Manual adjustments">
        {detail.adjustments.length === 0 && <Empty>None.</Empty>}
        {detail.adjustments.map((a, index) => (
          <p key={index}>
            {money(a.amount)} — {a.reason}
            {a.reversed ? " (reversed)" : ""}
          </p>
        ))}
      </Section>
    </div>
  )
}

interface OrderProps {
  module: RemittanceModule
  record: CodRecord | PrepaidRecord
  onClose: () => void
}

/** Full order / reconciliation details of one drilldown record. Read-only. */
export function RecordDetailDialog({ module, record, onClose }: OrderProps) {
  const query = useReconciliationDetail(module, record.order_id)
  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>Order {record.order_number}</DialogTitle>
          <DialogDescription>
            Reconciliation details and the evidence behind them.
          </DialogDescription>
        </DialogHeader>
        <Button asChild size="sm" variant="outline" className="w-fit">
          <Link href={`/orders/${record.order_id}`}>
            <ExternalLink className="size-4" />
            Open full order
          </Link>
        </Button>
        <QueryStates
          isLoading={query.isLoading}
          isError={query.isError}
          error={query.error}
          data={query.data}
          onRetry={() => void query.refetch()}
        >
          {(detail) => <DetailBody detail={detail} />}
        </QueryStates>
      </DialogContent>
    </Dialog>
  )
}

const KIND_LABEL: Record<UnmatchedRow["kind"], string> = {
  remittance_line: "Remittance line",
  gateway_line: "Settlement line",
  bank_credit: "Bank credit",
}

/** Everything known about one unmatched line / bank credit (it has no order). */
export function UnmatchedDetailDialog({
  row,
  isCod,
  onClose,
}: {
  row: UnmatchedRow
  isCod: boolean
  onClose: () => void
}) {
  const common: [string, React.ReactNode][] = [
    ["Type", KIND_LABEL[row.kind]],
    [isCod ? "Remittance reference / UTR" : "UTR / reference", text(row.reference)],
    [row.kind === "bank_credit" ? "Credit date" : "Settlement date", day(row.date)],
    [row.kind === "bank_credit" ? "Unlinked amount" : "Net amount", money(row.amount)],
  ]
  const line: [string, React.ReactNode][] =
    row.kind === "bank_credit"
      ? []
      : isCod
        ? [
            ["Courier partner", text(row.partner)],
            ["AWB", text(row.awb)],
            ["Order reference in report", text(row.order_ref)],
          ]
        : [
            ["Gateway", text(row.gateway)],
            ["Cashfree payment ID", text(row.cashfree_payment_id)],
            ["Transaction ID", text(row.transaction_id)],
            ["Settlement ID", text(row.settlement_id)],
            ["Order reference", text(row.order_ref)],
            ["Gross amount", money(row.gross_amount)],
            ["Charges", money(row.gateway_charges)],
            ["GST", money(row.gst_amount)],
            ["Refunds", money(row.refund_amount)],
          ]
  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>Unmatched {KIND_LABEL[row.kind].toLowerCase()}</DialogTitle>
          <DialogDescription>{row.reason}</DialogDescription>
        </DialogHeader>
        <Fields items={[...common, ...line]} />
        <Empty>No order is linked to this item, so there are no order details yet.</Empty>
      </DialogContent>
    </Dialog>
  )
}

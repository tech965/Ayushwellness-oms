"use client"

import * as React from "react"
import { toast } from "sonner"

import { PaginationBar } from "@/components/shared/pagination-bar"
import { QueryStates } from "@/components/shared/query-states"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { getApiErrorMessage } from "@/lib/api-client"
import { formatDate, formatDateTime } from "@/lib/format"
import { formatPercent } from "@/lib/percent"
import { rupees } from "@/lib/remittance-format"
import {
  downloadOriginalFile,
  useBankCredits,
  useLinkBankCredit,
  useRemittanceReport,
  useRemittanceSources,
  useRemittanceUnmatched,
  useRemittanceUploads,
  useSettlementLots,
} from "@/services/remittance"
import type {
  BankCreditRow,
  GatewayRow,
  PartnerRow,
  RemittanceFilters,
  RemittanceModule,
  UnmatchedRow,
} from "@/types/remittance"

import type { Selection } from "./remittance-dashboard"
import type { UploadKind } from "./upload-dialog"

interface Props {
  module: RemittanceModule
  filters: RemittanceFilters
  selection: Selection | null
  onSelect: (selection: Selection) => void
  canUpload: boolean
  canAdjust: boolean
  onUpload: (kind: UploadKind) => void
  /** COD only, and only for users who may adjust: open the manual-match dialog. */
  onMatchLine?: (row: UnmatchedRow) => void
}

/** A number in a report table that opens the records behind it. */
function Cell({
  value,
  label,
  onClick,
  className,
}: {
  value: React.ReactNode
  label: string
  onClick?: () => void
  className?: string
}) {
  return (
    <TableCell className={className ?? "text-right tabular-nums"}>
      {onClick ? (
        <button
          type="button"
          aria-label={label}
          className="hover:bg-accent rounded px-1.5 py-0.5 underline-offset-2 hover:underline"
          onClick={(event) => {
            event.stopPropagation()
            onClick()
          }}
        >
          {value}
        </button>
      ) : (
        value
      )}
    </TableCell>
  )
}

function Head({
  children,
  right = true,
}: {
  children: React.ReactNode
  right?: boolean
}) {
  return (
    <TableHead className={right ? "text-right whitespace-normal" : ""}>
      {children}
    </TableHead>
  )
}

function LinkDialog({
  module,
  credit,
  onClose,
}: {
  module: RemittanceModule
  credit: BankCreditRow
  onClose: () => void
}) {
  const [reference, setReference] = React.useState("")
  const [bankReference, setBankReference] = React.useState("")
  // Deliberately blank: the person verifying states the amount.
  const [amount, setAmount] = React.useState("")
  const [reason, setReason] = React.useState("")
  const link = useLinkBankCredit(module)
  const complete =
    reference.trim() !== "" &&
    bankReference.trim() !== "" &&
    Number(amount) > 0 &&
    reason.trim().length >= 3
  async function submit() {
    if (!complete) return
    try {
      await link.mutateAsync({
        bank_credit_id: credit.id,
        reference: reference.trim(),
        bank_reference: bankReference.trim(),
        amount: amount.trim(),
        reason: reason.trim(),
      })
      toast.success("Bank credit manually verified and linked.")
      onClose()
    } catch (error) {
      toast.error(getApiErrorMessage(error))
    }
  }
  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Manually verify bank credit</DialogTitle>
          <DialogDescription>
            {rupees(credit.amount)} credited{" "}
            {credit.credit_date ? formatDate(credit.credit_date) : ""}. Enter the{" "}
            {module === "cod" ? "remittance reference" : "settlement UTR"} it pays. It
            must exist in an uploaded report; up to {rupees(credit.unlinked_amount)} is
            unallocated, and no more than the reference still needs can be linked. This is
            recorded as <strong>Manually verified</strong> and audit logged. All fields
            are required.
          </DialogDescription>
        </DialogHeader>
        <form
          className="space-y-4"
          onSubmit={(event) => {
            event.preventDefault()
            void submit()
          }}
        >
          <div className="space-y-1.5">
            <Label htmlFor="link-ref">Reference</Label>
            <Input
              id="link-ref"
              value={reference}
              onChange={(e) => setReference(e.target.value)}
              required
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="link-bank-ref">Bank reference / UTR you verified</Label>
            <Input
              id="link-bank-ref"
              value={bankReference}
              onChange={(e) => setBankReference(e.target.value)}
              placeholder={credit.reference ?? undefined}
              maxLength={128}
              required
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="link-amount">Amount (₹)</Label>
            <Input
              id="link-amount"
              inputMode="decimal"
              value={amount}
              onChange={(e) => setAmount(e.target.value)}
              required
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="link-reason">Reason</Label>
            <Input
              id="link-reason"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              maxLength={255}
              required
            />
          </div>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={onClose}>
              Cancel
            </Button>
            <Button type="submit" disabled={link.isPending || !complete}>
              {link.isPending ? "Saving…" : "Verify and link"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

/** The additional reports: partner/gateway-wise, settlement date-wise,
 * ageing, bank-credit matching, unmatched transactions, uploaded files and
 * data sources. Counts and rows are clickable and open the exact records.
 */
export function RemittanceReports({
  module,
  filters,
  onSelect,
  canUpload,
  canAdjust,
  onUpload,
  onMatchLine,
}: Props) {
  const isCod = module === "cod"
  const [tab, setTab] = React.useState("partners")
  const [creditPage, setCreditPage] = React.useState(1)
  const [unmatchedPage, setUnmatchedPage] = React.useState(1)
  const [unmatchedKind, setUnmatchedKind] = React.useState<string | undefined>(undefined)
  const [lotPage, setLotPage] = React.useState(1)
  const [linking, setLinking] = React.useState<BankCreditRow | null>(null)

  const base = {
    date_from: filters.date_from,
    date_to: filters.date_to,
    status: undefined,
  }
  const partnerFilters = { ...base, [isCod ? "partner" : "gateway"]: undefined }
  const rows = useRemittanceReport<PartnerRow & GatewayRow>(
    module,
    isCod ? "partners" : "gateways",
    filters,
    tab === "partners"
  )
  const dates = useRemittanceReport<Record<string, string | number>>(
    module,
    "settlement-dates",
    filters,
    tab === "dates"
  )
  const ageing = useRemittanceReport<Record<string, string | number>>(
    module,
    "ageing",
    filters,
    tab === "ageing"
  )
  const credits = useBankCredits(module, filters, creditPage, 10, tab === "bank")
  const unmatched = useRemittanceUnmatched(
    module,
    filters,
    unmatchedKind,
    unmatchedPage,
    10,
    tab === "unmatched"
  )
  const lots = useSettlementLots(partnerFilters, lotPage, 10, !isCod && tab === "lots")
  const uploads = useRemittanceUploads(module, tab === "files")
  const sources = useRemittanceSources()

  return (
    <section className="space-y-3" aria-label="Reconciliation reports">
      <div>
        <h3 className="text-base font-semibold">Reports</h3>
        <p className="text-muted-foreground text-xs">
          Same date range and filters as above. Click a count or row to open its records.
        </p>
      </div>
      <Tabs value={tab} onValueChange={setTab}>
        {/* The shared list is one row high; the same variant is needed to
            override it, or wrapped tabs sit under the table on phones. */}
        <TabsList className="flex-wrap group-data-horizontal/tabs:h-auto">
          <TabsTrigger value="partners">
            {isCod ? "Partner-wise" : "Gateway-wise"}
          </TabsTrigger>
          <TabsTrigger value="dates">Settlement date-wise</TabsTrigger>
          <TabsTrigger value="ageing">
            {isCod ? "Pending ageing" : "Settlement ageing"}
          </TabsTrigger>
          <TabsTrigger value="bank">Bank credit matching</TabsTrigger>
          <TabsTrigger value="unmatched">Unmatched report</TabsTrigger>
          {!isCod && <TabsTrigger value="lots">Cashfree settlement lots</TabsTrigger>}
          <TabsTrigger value="files">Files &amp; sources</TabsTrigger>
        </TabsList>

        <TabsContent value="partners" className="min-w-0">
          <QueryStates
            isLoading={rows.isLoading}
            isError={rows.isError}
            error={rows.error}
            data={rows.data}
            onRetry={() => rows.refetch()}
            isEmpty={(d) => d.length === 0}
            emptyTitle="No data in this range"
          >
            {(data) => (
              <div className="bg-card overflow-x-auto rounded-lg border">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <Head right={false}>
                        {isCod ? "Shipping partner" : "Payment gateway"}
                      </Head>
                      <Head>{isCod ? "Delivered" : "Orders"}</Head>
                      <Head>{isCod ? "Expected" : "Gross"}</Head>
                      {isCod ? (
                        <>
                          <Head>COD settled (courier)</Head>
                          <Head>COD bank confirmed</Head>
                          <Head>Pending</Head>
                          <Head>Short</Head>
                          <Head>Excess</Head>
                        </>
                      ) : (
                        <>
                          <Head>Charges</Head>
                          <Head>GST</Head>
                          <Head>Refunds</Head>
                          <Head>Expected net</Head>
                          <Head>Settlement received (gateway)</Head>
                          <Head>Prepaid bank confirmed</Head>
                          <Head>Pending</Head>
                        </>
                      )}
                      <Head>Matched</Head>
                      <Head>Unmatched lines</Head>
                      <Head>Completion %</Head>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {data.map((row) => {
                      const name = isCod ? row.partner : row.gateway
                      const open = (kpi?: string) =>
                        onSelect({ type: "partner", key: name, kpi, title: name })
                      return (
                        <TableRow
                          key={name}
                          className="hover:bg-muted/50 cursor-pointer"
                          onClick={() => open()}
                        >
                          <TableCell className="font-medium">{name}</TableCell>
                          <Cell
                            label={`${name} orders`}
                            value={isCod ? row.delivered_orders : row.orders}
                            onClick={() => open()}
                          />
                          <TableCell className="text-right tabular-nums">
                            {rupees(isCod ? row.expected_amount : row.gross_amount)}
                          </TableCell>
                          {isCod ? (
                            <>
                              <TableCell className="text-right tabular-nums">
                                {rupees(row.remittance_amount)}
                              </TableCell>
                              <TableCell className="text-right tabular-nums">
                                {rupees(row.amount_received)}
                              </TableCell>
                              <Cell
                                label={`${name} pending orders`}
                                value={row.pending_orders}
                                onClick={() => open("pending")}
                              />
                              <Cell
                                label={`${name} short orders`}
                                value={row.short_orders}
                                onClick={() => open("short")}
                              />
                              <Cell
                                label={`${name} excess orders`}
                                value={row.excess_orders}
                                onClick={() => open("excess")}
                              />
                            </>
                          ) : (
                            <>
                              <TableCell className="text-right tabular-nums">
                                {rupees(row.gateway_charges)}
                              </TableCell>
                              <TableCell className="text-right tabular-nums">
                                {rupees(row.gst_amount)}
                              </TableCell>
                              <TableCell className="text-right tabular-nums">
                                {rupees(row.refund_amount)}
                              </TableCell>
                              <TableCell className="text-right tabular-nums">
                                {rupees(row.expected_net)}
                              </TableCell>
                              <TableCell className="text-right tabular-nums">
                                {rupees(row.reported_settlement)}
                              </TableCell>
                              <TableCell className="text-right tabular-nums">
                                {rupees(row.bank_settlement)}
                              </TableCell>
                              <Cell
                                label={`${name} pending orders`}
                                value={row.pending_orders}
                                onClick={() => open("pending")}
                              />
                            </>
                          )}
                          <Cell
                            label={`${name} reconciled orders`}
                            value={row.matched_orders}
                            onClick={() => open("reconciled")}
                          />
                          <Cell
                            label={`${name} unmatched lines`}
                            value={row.unmatched_lines}
                            onClick={() =>
                              onSelect({
                                type: "unmatched",
                                title: `${name} — unmatched lines`,
                                partner: name,
                                kind: isCod ? "remittance_line" : "gateway_line",
                              })
                            }
                          />
                          <TableCell className="text-right tabular-nums">
                            {formatPercent(row.completion_percent)}
                          </TableCell>
                        </TableRow>
                      )
                    })}
                  </TableBody>
                </Table>
              </div>
            )}
          </QueryStates>
        </TabsContent>

        <TabsContent value="dates" className="min-w-0">
          <QueryStates
            isLoading={dates.isLoading}
            isError={dates.isError}
            error={dates.error}
            data={dates.data}
            onRetry={() => dates.refetch()}
            isEmpty={(d) => d.length === 0}
            emptyTitle="No settlements in this range"
          >
            {(data) => (
              <div className="bg-card overflow-x-auto rounded-lg border">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <Head right={false}>Settlement date</Head>
                      <Head>Lines</Head>
                      <Head>
                        {isCod
                          ? "COD settled, courier (incl. unmatched)"
                          : "Gateway net settled (incl. unmatched)"}
                      </Head>
                      {!isCod && <Head>Charges</Head>}
                      {!isCod && <Head>GST</Head>}
                      <Head>{isCod ? "COD" : "Prepaid"} bank confirmed</Head>
                      <Head>Unmatched lines</Head>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {data.map((row) => {
                      const day = String(row.date)
                      const open =
                        day === "No date"
                          ? undefined
                          : () => onSelect({ type: "settled", day })
                      return (
                        <TableRow
                          key={day}
                          className={open ? "hover:bg-muted/50 cursor-pointer" : ""}
                          onClick={open}
                        >
                          <TableCell className="font-medium">
                            {day === "No date" ? day : formatDate(day)}
                          </TableCell>
                          <Cell label={`${day} lines`} value={row.lines} onClick={open} />
                          <TableCell className="text-right tabular-nums">
                            {rupees(String(isCod ? row.amount : row.net_amount))}
                          </TableCell>
                          {!isCod && (
                            <TableCell className="text-right tabular-nums">
                              {rupees(String(row.gateway_charges))}
                            </TableCell>
                          )}
                          {!isCod && (
                            <TableCell className="text-right tabular-nums">
                              {rupees(String(row.gst_amount))}
                            </TableCell>
                          )}
                          <TableCell className="text-right tabular-nums">
                            {rupees(String(row.bank_confirmed_amount))}
                          </TableCell>
                          <Cell
                            label={`${day} unmatched lines`}
                            value={row.unmatched_lines}
                            onClick={() =>
                              onSelect({
                                type: "unmatched",
                                title: `Unmatched lines settled ${day}`,
                                kind: isCod ? "remittance_line" : "gateway_line",
                              })
                            }
                          />
                        </TableRow>
                      )
                    })}
                  </TableBody>
                </Table>
              </div>
            )}
          </QueryStates>
        </TabsContent>

        <TabsContent value="ageing" className="min-w-0">
          <QueryStates
            isLoading={ageing.isLoading}
            isError={ageing.isError}
            error={ageing.error}
            data={ageing.data}
            onRetry={() => ageing.refetch()}
          >
            {(data) => (
              <div className="bg-card overflow-x-auto rounded-lg border">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <Head right={false}>
                        Age since {isCod ? "delivery" : "payment"}
                      </Head>
                      <Head>Pending orders</Head>
                      <Head>{isCod ? "Pending amount" : "Pending amount (gross)"}</Head>
                      {isCod && <Head>Short orders</Head>}
                      {isCod && <Head>Short amount</Head>}
                      <Head>Awaiting bank</Head>
                      <Head>Amount awaiting bank confirmation</Head>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {data.map((row) => {
                      const bucket = String(row.bucket)
                      const open = () => onSelect({ type: "age", bucket })
                      return (
                        <TableRow
                          key={bucket}
                          className="hover:bg-muted/50 cursor-pointer"
                          onClick={open}
                        >
                          <TableCell className="font-medium">{bucket}</TableCell>
                          <Cell
                            label={`${bucket} pending orders`}
                            value={row.pending_orders}
                            onClick={open}
                          />
                          <TableCell className="text-right tabular-nums">
                            {rupees(String(row.pending_amount))}
                          </TableCell>
                          {isCod && (
                            <Cell
                              label={`${bucket} short orders`}
                              value={row.short_orders}
                              onClick={open}
                            />
                          )}
                          {isCod && (
                            <TableCell className="text-right tabular-nums">
                              {rupees(String(row.short_amount))}
                            </TableCell>
                          )}
                          <Cell
                            label={`${bucket} awaiting bank`}
                            value={row.awaiting_bank_orders}
                            onClick={open}
                          />
                          <TableCell className="text-right tabular-nums">
                            {rupees(String(row.awaiting_bank_amount))}
                          </TableCell>
                        </TableRow>
                      )
                    })}
                  </TableBody>
                </Table>
              </div>
            )}
          </QueryStates>
        </TabsContent>

        <TabsContent value="bank" className="min-w-0 space-y-2">
          <QueryStates
            isLoading={credits.isLoading}
            isError={credits.isError}
            error={credits.error}
            data={credits.data}
            onRetry={() => credits.refetch()}
            isEmpty={(d) => d.data.length === 0}
            emptyTitle="No bank credits imported in this range"
            emptyDescription="Upload a bank statement to match remittances against actual credits."
          >
            {(result) => (
              <>
                <div className="bg-card overflow-x-auto rounded-lg border">
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <Head right={false}>Credit date</Head>
                        <Head right={false}>Reference / UTR</Head>
                        <Head right={false}>Narration</Head>
                        <Head>Amount</Head>
                        <Head>Statement-matched</Head>
                        <Head>Manually verified</Head>
                        <Head>Not linked</Head>
                        <Head right={false}>State</Head>
                        {canAdjust && <Head right={false}>{""}</Head>}
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {result.data.map((c) => (
                        <TableRow key={c.id}>
                          <TableCell>
                            {c.credit_date ? formatDate(c.credit_date) : "—"}
                          </TableCell>
                          <TableCell>{c.reference ?? "—"}</TableCell>
                          <TableCell className="text-muted-foreground max-w-48 truncate text-xs">
                            {c.narration ?? "—"}
                          </TableCell>
                          <TableCell className="text-right tabular-nums">
                            {rupees(c.amount)}
                          </TableCell>
                          <TableCell className="text-right tabular-nums">
                            {rupees(c.statement_matched_amount)}
                          </TableCell>
                          <TableCell className="text-right tabular-nums">
                            {rupees(c.manually_verified_amount)}
                            {c.links
                              ?.filter((l) => l.verification === "manually_verified")
                              .map((l) => (
                                <span
                                  key={l.id}
                                  className="text-muted-foreground block max-w-56 text-xs whitespace-normal"
                                >
                                  {l.bank_reference ?? "—"}: {l.reason ?? "—"}
                                </span>
                              ))}
                          </TableCell>
                          <TableCell className="text-right tabular-nums">
                            {rupees(c.unlinked_amount)}
                          </TableCell>
                          <TableCell>
                            <Badge variant="outline">
                              {c.link_state === "linked"
                                ? "Linked"
                                : c.link_state === "partially_linked"
                                  ? "Partly linked"
                                  : "Not linked"}
                            </Badge>
                          </TableCell>
                          {canAdjust && (
                            <TableCell>
                              {c.link_state !== "linked" && (
                                <Button
                                  size="sm"
                                  variant="ghost"
                                  onClick={() => setLinking(c)}
                                >
                                  Link
                                </Button>
                              )}
                            </TableCell>
                          )}
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </div>
                <PaginationBar meta={result.meta} onPageChange={setCreditPage} />
              </>
            )}
          </QueryStates>
        </TabsContent>

        <TabsContent value="unmatched" className="min-w-0 space-y-2">
          <div className="flex gap-2">
            {[
              { label: "All", value: undefined },
              {
                label: isCod ? "Remittance lines" : "Settlement lines",
                value: isCod ? "remittance_line" : "gateway_line",
              },
              { label: "Bank credits", value: "bank_credit" },
            ].map((chip) => (
              <Button
                key={chip.label}
                size="sm"
                variant={unmatchedKind === chip.value ? "default" : "outline"}
                onClick={() => {
                  setUnmatchedKind(chip.value)
                  setUnmatchedPage(1)
                }}
              >
                {chip.label}
              </Button>
            ))}
          </div>
          <QueryStates
            isLoading={unmatched.isLoading}
            isError={unmatched.isError}
            error={unmatched.error}
            data={unmatched.data}
            onRetry={() => unmatched.refetch()}
            isEmpty={(d) => d.data.length === 0}
            emptyTitle="Nothing is unmatched"
          >
            {(result) => (
              <>
                <div className="bg-card overflow-x-auto rounded-lg border">
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <Head right={false}>Type</Head>
                        <Head right={false}>{isCod ? "Partner" : "Gateway"}</Head>
                        <Head right={false}>{isCod ? "AWB" : "Transaction ID"}</Head>
                        <Head right={false}>Reference / UTR</Head>
                        <Head>Amount</Head>
                        <Head right={false}>Date</Head>
                        <Head right={false}>Why</Head>
                        {onMatchLine && <Head right={false}>{""}</Head>}
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {result.data.map((u) => (
                        <TableRow key={`${u.kind}-${u.id}`}>
                          <TableCell>
                            {u.kind === "bank_credit"
                              ? "Bank credit"
                              : isCod
                                ? "Remittance line"
                                : "Settlement line"}
                          </TableCell>
                          <TableCell>{u.partner ?? u.gateway ?? "—"}</TableCell>
                          <TableCell>{u.awb ?? u.transaction_id ?? "—"}</TableCell>
                          <TableCell>{u.reference ?? "—"}</TableCell>
                          <TableCell className="text-right tabular-nums">
                            {rupees(u.amount)}
                          </TableCell>
                          <TableCell>{u.date ? formatDate(u.date) : "—"}</TableCell>
                          <TableCell className="text-muted-foreground text-xs">
                            {u.reason}
                          </TableCell>
                          {onMatchLine && (
                            <TableCell>
                              {u.kind === "remittance_line" && (
                                <Button
                                  size="sm"
                                  variant="outline"
                                  onClick={() => onMatchLine(u)}
                                >
                                  Match to order
                                </Button>
                              )}
                            </TableCell>
                          )}
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </div>
                <PaginationBar meta={result.meta} onPageChange={setUnmatchedPage} />
              </>
            )}
          </QueryStates>
        </TabsContent>

        {!isCod && (
          <TabsContent value="lots" className="min-w-0 space-y-2">
            <p className="text-muted-foreground text-xs">
              Settlement lots already fetched through the Cashfree integration, with
              whether a bank credit has been linked to each UTR.
            </p>
            <QueryStates
              isLoading={lots.isLoading}
              isError={lots.isError}
              error={lots.error}
              data={lots.data}
              onRetry={() => lots.refetch()}
              isEmpty={(d) => d.data.length === 0}
              emptyTitle="No Cashfree settlement lots synced"
              emptyDescription="Sync settlements from the Payments page, or upload a settlement report."
            >
              {(result) => (
                <>
                  <div className="bg-card overflow-x-auto rounded-lg border">
                    <Table>
                      <TableHeader>
                        <TableRow>
                          <Head right={false}>Settlement</Head>
                          <Head right={false}>UTR</Head>
                          <Head right={false}>Processed</Head>
                          <Head>Payments</Head>
                          <Head>Charges</Head>
                          <Head>GST</Head>
                          <Head>Settled</Head>
                          <Head>Bank credited</Head>
                          <Head right={false}>Bank</Head>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {result.data.map((l) => (
                          <TableRow key={l.settlement_id}>
                            <TableCell>{l.settlement_id}</TableCell>
                            <TableCell>{l.utr ?? "—"}</TableCell>
                            <TableCell>
                              {l.processed_on ? formatDate(l.processed_on) : "—"}
                            </TableCell>
                            <TableCell className="text-right tabular-nums">
                              {rupees(l.payment_amount)}
                            </TableCell>
                            <TableCell className="text-right tabular-nums">
                              {rupees(l.charges)}
                            </TableCell>
                            <TableCell className="text-right tabular-nums">
                              {rupees(l.gst)}
                            </TableCell>
                            <TableCell className="text-right tabular-nums">
                              {rupees(l.amount_settled)}
                            </TableCell>
                            <TableCell className="text-right tabular-nums">
                              {rupees(l.bank_credited)}
                            </TableCell>
                            <TableCell>
                              <Badge variant="outline">
                                {l.bank_state === "linked"
                                  ? "Linked"
                                  : l.bank_state === "partially_linked"
                                    ? "Partly linked"
                                    : "Not linked"}
                              </Badge>
                            </TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  </div>
                  <PaginationBar meta={result.meta} onPageChange={setLotPage} />
                </>
              )}
            </QueryStates>
          </TabsContent>
        )}

        <TabsContent value="files" className="min-w-0 space-y-4">
          <div className="grid gap-2 sm:grid-cols-2">
            {(sources.data?.[module] ?? []).map((s) => (
              <div key={s.key} className="bg-card rounded-lg border p-3 text-sm">
                <div className="flex items-center justify-between gap-2">
                  <span className="font-medium">{s.label}</span>
                  <Badge variant="outline">
                    {s.api_supported
                      ? s.api_configured
                        ? "API connected"
                        : "Not Configured"
                      : "Upload only"}
                  </Badge>
                </div>
                <p className="text-muted-foreground mt-1 text-xs">{s.note}</p>
                {s.last_synced_at && (
                  <p className="text-muted-foreground mt-1 text-xs">
                    Last synced {formatDateTime(s.last_synced_at)}
                  </p>
                )}
              </div>
            ))}
          </div>
          {canUpload && (
            <div className="flex gap-2">
              <Button size="sm" onClick={() => onUpload("report")}>
                {isCod
                  ? "Upload remittance report"
                  : "Upload settlement report (fallback)"}
              </Button>
              <Button size="sm" variant="outline" onClick={() => onUpload("bank")}>
                Upload bank statement
              </Button>
            </div>
          )}
          <QueryStates
            isLoading={uploads.isLoading}
            isError={uploads.isError}
            error={uploads.error}
            data={uploads.data}
            onRetry={() => uploads.refetch()}
            isEmpty={(d) => d.length === 0}
            emptyTitle="No files uploaded yet"
          >
            {(files) => (
              <div className="bg-card overflow-x-auto rounded-lg border">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <Head right={false}>Uploaded</Head>
                      <Head right={false}>Type</Head>
                      <Head right={false}>Source</Head>
                      <Head right={false}>File</Head>
                      <Head>Rows</Head>
                      <Head>Imported</Head>
                      <Head>Duplicates</Head>
                      <Head>Rejected</Head>
                      <Head right={false}>{""}</Head>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {files.map((f) => (
                      <TableRow key={f.id}>
                        <TableCell>{formatDateTime(f.created_at)}</TableCell>
                        <TableCell>
                          {f.module === "bank"
                            ? "Bank statement"
                            : isCod
                              ? "Remittance report"
                              : "Settlement report"}
                        </TableCell>
                        <TableCell>{f.source_name}</TableCell>
                        <TableCell>{f.filename}</TableCell>
                        <TableCell className="text-right tabular-nums">
                          {f.row_count}
                        </TableCell>
                        <TableCell className="text-right tabular-nums">
                          {f.accepted_count}
                        </TableCell>
                        <TableCell className="text-right tabular-nums">
                          {f.duplicate_count}
                        </TableCell>
                        <TableCell className="text-right tabular-nums">
                          {f.rejected_count}
                        </TableCell>
                        <TableCell>
                          <Button
                            size="sm"
                            variant="ghost"
                            onClick={() =>
                              void downloadOriginalFile(f.id, f.filename).catch((e) =>
                                toast.error(getApiErrorMessage(e))
                              )
                            }
                          >
                            Original file
                          </Button>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            )}
          </QueryStates>
        </TabsContent>
      </Tabs>
      {linking && (
        <LinkDialog module={module} credit={linking} onClose={() => setLinking(null)} />
      )}
    </section>
  )
}

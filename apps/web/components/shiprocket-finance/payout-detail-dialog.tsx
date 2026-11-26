"use client"

import { QueryStates } from "@/components/shared/query-states"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { formatDate, formatMoney } from "@/lib/format"
import { usePayoutDetail } from "@/services/shiprocket-finance"

const money = (value: string | null) => (value === null ? "—" : formatMoney(value))

/** One payout: what the report says, the bank credits that carry its UTR, and
 * the per-AWB remittance lines from the COD report (Module 6) under the same
 * reference. Read-only: nothing here changes any reconciliation.
 */
export function PayoutDetailDialog({ id, onClose }: { id: string; onClose: () => void }) {
  const query = usePayoutDetail(id)
  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>Payout details</DialogTitle>
          <DialogDescription>
            As reported by Shiprocket, and what the bank statement shows.
          </DialogDescription>
        </DialogHeader>
        <QueryStates
          isLoading={query.isLoading}
          isError={query.isError}
          error={query.error}
          data={query.data}
          onRetry={() => void query.refetch()}
        >
          {({ payout, bank_credits, awb_lines, awb_lines_total }) => (
            <div className="space-y-4 text-sm">
              <dl className="grid grid-cols-1 gap-x-6 gap-y-1.5 sm:grid-cols-2">
                {(
                  [
                    ["CRF / payout ID", payout.crf_id ?? "—"],
                    ["UTR", payout.utr ?? "—"],
                    [
                      "Payout date",
                      payout.payout_date ? formatDate(payout.payout_date) : "—",
                    ],
                    ["Status (reported)", payout.status ?? "—"],
                    ["COD amount", money(payout.cod_amount)],
                    ["Freight deducted", money(payout.freight_deduction)],
                    ["Other deductions", money(payout.other_deductions)],
                    ["Remittance amount", money(payout.net_amount)],
                    [
                      "COD − deductions − remittance",
                      money(payout.unexplained_difference),
                    ],
                    ["Bank comparison", payout.bank_state_label],
                  ] as [string, string][]
                ).map(([label, value]) => (
                  <div key={label} className="flex justify-between gap-3 border-b pb-1">
                    <dt className="text-muted-foreground">{label}</dt>
                    <dd className="font-medium break-all">{value}</dd>
                  </div>
                ))}
              </dl>
              <section>
                <h4 className="font-semibold">Bank credits with this UTR</h4>
                {bank_credits.length === 0 && (
                  <p className="text-muted-foreground text-xs">
                    None. A reported payout is not proof the money arrived.
                  </p>
                )}
                {bank_credits.map((c, index) => (
                  <p key={index}>
                    {c.reference}: {money(c.amount)}
                    {c.credit_date ? ` on ${formatDate(c.credit_date)}` : ""}
                  </p>
                ))}
              </section>
              <section>
                <h4 className="font-semibold">
                  AWB lines under the same reference ({awb_lines.length} ·{" "}
                  {money(awb_lines_total)})
                </h4>
                {awb_lines.length === 0 && (
                  <p className="text-muted-foreground text-xs">
                    None. Per-AWB amounts come from the COD remittance report in Module 6.
                  </p>
                )}
                {awb_lines.slice(0, 50).map((l, index) => (
                  <p key={index}>
                    {l.awb ?? "—"} · {l.order_ref ?? "—"} · {money(l.amount)}
                    {l.matched_to_order ? "" : " · not matched to an order"}
                  </p>
                ))}
              </section>
            </div>
          )}
        </QueryStates>
      </DialogContent>
    </Dialog>
  )
}

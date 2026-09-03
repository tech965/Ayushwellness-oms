import { CheckCircle2, Clock, IndianRupee, Receipt, XCircle } from "lucide-react"

import { KpiCard } from "@/components/dashboard/kpi-card"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { getApiErrorMessage } from "@/lib/api-client"
import { formatMoney } from "@/lib/format"
import type { CashfreePaymentOverview } from "@/types/cashfree"

function money(value: string): string {
  return formatMoney(value)
}
function count(value: string): string {
  return Number(value).toLocaleString("en-IN")
}

interface PaymentOverviewCardsProps {
  data: CashfreePaymentOverview | undefined
  /** Builds an `/payments`-filtered drill-down link for one KPI. */
  hrefFor: (params: Record<string, string>) => string
  /** A genuine fetch failure -- when true, an "Unable to load Cashfree
   * data" banner replaces the tiles entirely. Real Cashfree data that
   * happens to be all-zero is never affected: this only fires when the
   * request itself failed, not when it succeeded with zero records.
   */
  isError?: boolean
  error?: unknown
  onRetry?: () => void
}

/** The five Cashfree payment KPI tiles — reuses `KpiCard` exactly like
 * every other dashboard KPI row (period-over-period %, drill-down link).
 * Fed by `usePaymentOverview` called with `provider: "cashfree"` (see
 * `app/(dashboard)/payments/page.tsx`) -- always Cashfree-only,
 * regardless of the page's own general provider filter.
 */
export function PaymentOverviewCards({
  data,
  hrefFor,
  isError,
  error,
  onRetry,
}: PaymentOverviewCardsProps) {
  if (isError) {
    return (
      <Alert variant="destructive">
        <AlertTitle>Unable to load Cashfree data</AlertTitle>
        <AlertDescription className="flex flex-col gap-3">
          <span>{getApiErrorMessage(error)}</span>
          {onRetry && (
            <Button variant="outline" size="sm" className="w-fit" onClick={onRetry}>
              Retry
            </Button>
          )}
        </AlertDescription>
      </Alert>
    )
  }

  return (
    <section className="grid grid-cols-2 gap-4 lg:grid-cols-5">
      <KpiCard
        label="Total Payments"
        icon={Receipt}
        kpi={data?.total_payments}
        format={count}
        href={hrefFor({})}
        accent="slate"
      />
      <KpiCard
        label="Paid"
        icon={CheckCircle2}
        kpi={data?.paid_payments}
        format={count}
        href={hrefFor({ status: "paid" })}
        accent="emerald"
      />
      <KpiCard
        label="Pending"
        icon={Clock}
        kpi={data?.pending_payments}
        format={count}
        href={hrefFor({ status: "pending" })}
        accent="amber"
        invert
      />
      <KpiCard
        label="Failed"
        icon={XCircle}
        kpi={data?.failed_payments}
        format={count}
        href={hrefFor({ status: "failed" })}
        accent="orange"
        invert
      />
      <KpiCard
        // `data.total_amount` only ever sums PAID payments (see
        // `PaymentService.get_payment_overview`) -- "Paid Amount" says
        // that unambiguously; pending/failed amounts were never included.
        label="Paid Amount"
        icon={IndianRupee}
        kpi={data?.total_amount}
        format={money}
        href={hrefFor({ status: "paid" })}
        accent="blue"
      />
    </section>
  )
}

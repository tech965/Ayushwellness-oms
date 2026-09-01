import { CheckCircle2, Clock, IndianRupee, Receipt, XCircle } from "lucide-react"

import { KpiCard } from "@/components/dashboard/kpi-card"
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
}

/** The five Cashfree payment KPI tiles — reuses `KpiCard` exactly like
 * every other dashboard KPI row (period-over-period %, drill-down link).
 */
export function PaymentOverviewCards({ data, hrefFor }: PaymentOverviewCardsProps) {
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
        label="Total Amount"
        icon={IndianRupee}
        kpi={data?.total_amount}
        format={money}
        href={hrefFor({ status: "paid" })}
        accent="blue"
      />
    </section>
  )
}

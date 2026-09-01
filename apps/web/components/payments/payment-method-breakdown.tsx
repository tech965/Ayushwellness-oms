import { StatusDonutCard } from "@/components/dashboard/status-donut-card"
import type { CashfreePaymentMethodBreakdown } from "@/types/cashfree"

interface PaymentMethodBreakdownProps {
  data: CashfreePaymentMethodBreakdown | undefined
  isLoading: boolean
  hrefFor: (paymentMethod: string) => string
}

/** Cashfree payment-method distribution (UPI/card/netbanking/...) — reuses
 * the same `StatusDonut` engine every other status/type breakdown on the
 * dashboard already uses, via the `payment_method` tone domain
 * (`lib/status-styles.ts`).
 */
export function PaymentMethodBreakdown({
  data,
  isLoading,
  hrefFor,
}: PaymentMethodBreakdownProps) {
  const rows = (data?.items ?? []).map((item) => ({
    status: item.payment_method,
    count: item.count,
  }))

  return (
    <StatusDonutCard
      title="Payment Method"
      domain="payment_method"
      data={rows}
      isLoading={isLoading}
      hrefFor={hrefFor}
      centerLabel="Payments"
    />
  )
}

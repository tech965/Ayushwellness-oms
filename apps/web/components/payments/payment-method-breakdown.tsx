import { StatusDonutCard } from "@/components/dashboard/status-donut-card"
import type { CashfreePaymentMethodBreakdown } from "@/types/cashfree"

interface PaymentMethodBreakdownProps {
  data: CashfreePaymentMethodBreakdown | undefined
  isLoading: boolean
  hrefFor: (paymentMethod: string) => string
}

/** Payment-method distribution (UPI/card/netbanking/... for Cashfree
 * rows; falls back to cod/prepaid for Shopify rows, which never carry
 * the finer-grained metadata) — reuses the same `StatusDonut` engine
 * every other status/type breakdown on the dashboard already uses, via
 * the `payment_method` tone domain (`lib/status-styles.ts`). Fed by
 * `usePaymentMethodBreakdown` (provider-agnostic); still typed as
 * `CashfreePaymentMethodBreakdown` purely because it's the same
 * response shape, reused rather than duplicated.
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

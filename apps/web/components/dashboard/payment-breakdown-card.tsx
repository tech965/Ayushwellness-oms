import { Separator } from "@/components/ui/separator"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { StatusDonut } from "@/components/dashboard/status-donut-card"
import type { StatusCount } from "@/types/analytics"

interface PaymentBreakdownCardProps {
  paymentType: StatusCount[] | undefined
  paymentStatus: StatusCount[] | undefined
  isLoading: boolean
  hrefForType: (status: string) => string
  hrefForStatus: (status: string) => string
}

/** The reference design shows one "Payment Breakdown" card with Prepaid/COD
 * and Paid/Unpaid together — but those are two independent distributions in
 * the real API (`Breakdowns.payment_type` vs `Breakdowns.payment_status`),
 * whose percentages don't share a denominator. Rendering them as two
 * labeled donuts inside one card keeps the visual language while keeping
 * every percentage truthful.
 */
export function PaymentBreakdownCard({
  paymentType,
  paymentStatus,
  isLoading,
  hrefForType,
  hrefForStatus,
}: PaymentBreakdownCardProps) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Payment Breakdown</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <div>
          <p className="text-muted-foreground mb-2 text-xs font-semibold tracking-wide uppercase">
            Payment Type
          </p>
          <StatusDonut
            domain="payment"
            data={paymentType}
            isLoading={isLoading}
            hrefFor={hrefForType}
            centerLabel="Orders"
          />
        </div>
        <Separator />
        <div>
          <p className="text-muted-foreground mb-2 text-xs font-semibold tracking-wide uppercase">
            Payment Status
          </p>
          <StatusDonut
            domain="payment"
            data={paymentStatus}
            isLoading={isLoading}
            hrefFor={hrefForStatus}
            centerLabel="Orders"
          />
        </div>
      </CardContent>
    </Card>
  )
}

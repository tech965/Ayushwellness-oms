import { Check, X } from "lucide-react"

import { cn } from "@/lib/utils"
import type { PaymentDetail } from "@/types/payment"

type StepState = "done" | "current" | "upcoming" | "failed"

interface Step {
  key: string
  label: string
  state: StepState
}

interface PaymentStatusFlowProps {
  payment: Pick<PaymentDetail, "status" | "payment_session_id" | "transactions">
}

/** Purely presentational — derives where this one payment currently sits
 * in Created -> Checkout -> Pending -> Success/Failed -> Webhook -> OMS
 * Update from fields already on the payment/transaction data the detail
 * page already fetched. No new backend state.
 */
export function PaymentStatusFlow({ payment }: PaymentStatusFlowProps) {
  const isTerminal = payment.status === "paid" || payment.status === "failed"
  const isPaid = payment.status === "paid"
  const hasCheckout = Boolean(payment.payment_session_id)
  const hasWebhookOrReconcileEvent = payment.transactions.length > 0

  const steps: Step[] = [
    { key: "created", label: "Created", state: "done" },
    {
      key: "checkout",
      label: "Checkout",
      state: hasCheckout ? "done" : isTerminal ? "done" : "current",
    },
    {
      key: "pending",
      label: "Pending",
      state: isTerminal ? "done" : hasCheckout ? "current" : "upcoming",
    },
    {
      key: "outcome",
      label: isPaid ? "Success" : payment.status === "failed" ? "Failed" : "Success / Failed",
      state: isPaid ? "done" : payment.status === "failed" ? "failed" : "upcoming",
    },
    {
      key: "webhook",
      label: "Webhook",
      state: hasWebhookOrReconcileEvent ? "done" : isTerminal ? "current" : "upcoming",
    },
    {
      key: "oms_update",
      label: "OMS Update",
      state: isPaid ? "done" : payment.status === "failed" ? "failed" : "upcoming",
    },
  ]

  return (
    <div className="flex items-start">
      {steps.map((step, index) => (
        <div key={step.key} className="flex flex-1 items-start last:flex-none">
          <div className="flex flex-col items-center gap-1.5">
            <span
              className={cn(
                "flex size-7 shrink-0 items-center justify-center rounded-full border-2 text-xs font-semibold",
                step.state === "done" && "border-success bg-success text-success-foreground",
                step.state === "current" && "border-primary text-primary animate-pulse",
                step.state === "failed" && "border-danger bg-danger text-danger-foreground",
                step.state === "upcoming" && "border-border text-muted-foreground"
              )}
            >
              {step.state === "done" ? (
                <Check className="size-4" />
              ) : step.state === "failed" ? (
                <X className="size-4" />
              ) : (
                index + 1
              )}
            </span>
            <span
              className={cn(
                "max-w-[5.5rem] text-center text-[0.6875rem] leading-tight font-medium",
                step.state === "upcoming" ? "text-muted-foreground" : "text-foreground"
              )}
            >
              {step.label}
            </span>
          </div>
          {index < steps.length - 1 && (
            <div
              className={cn(
                "mt-3.5 h-0.5 flex-1",
                step.state === "done" ? "bg-success" : "bg-border"
              )}
            />
          )}
        </div>
      ))}
    </div>
  )
}

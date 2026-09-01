import { describe, expect, it } from "vitest"
import { render, screen } from "@testing-library/react"

import { PaymentStatusFlow } from "@/components/payments/payment-status-flow"

describe("PaymentStatusFlow", () => {
  it("shows Pending as the current step before checkout is created", () => {
    render(
      <PaymentStatusFlow
        payment={{ status: "pending", payment_session_id: null, transactions: [] }}
      />
    )
    expect(screen.getByText("Success / Failed")).toBeInTheDocument()
  })

  it("shows Success once the payment is paid", () => {
    render(
      <PaymentStatusFlow
        payment={{
          status: "paid",
          payment_session_id: "session_1",
          transactions: [
            {
              id: "t1",
              payment_id: "p1",
              gateway: "cashfree",
              gateway_transaction_id: "cfpay_1",
              status: "paid",
              amount: "500.00",
              created_at: "2026-02-01T10:05:00Z",
              event_type: "PAYMENT_SUCCESS_WEBHOOK",
              payment_method: "upi",
              error_reason: null,
            },
          ],
        }}
      />
    )
    expect(screen.getByText("Success")).toBeInTheDocument()
    expect(screen.getByText("OMS Update")).toBeInTheDocument()
    expect(screen.queryByText("Success / Failed")).not.toBeInTheDocument()
  })

  it("shows Failed once the payment has failed", () => {
    render(
      <PaymentStatusFlow
        payment={{
          status: "failed",
          payment_session_id: "session_1",
          transactions: [
            {
              id: "t1",
              payment_id: "p1",
              gateway: "cashfree",
              gateway_transaction_id: "cfpay_1",
              status: "failed",
              amount: "500.00",
              created_at: "2026-02-01T10:05:00Z",
              event_type: "PAYMENT_FAILED_WEBHOOK",
              payment_method: "upi",
              error_reason: "Insufficient funds",
            },
          ],
        }}
      />
    )
    expect(screen.getByText("Failed")).toBeInTheDocument()
  })
})

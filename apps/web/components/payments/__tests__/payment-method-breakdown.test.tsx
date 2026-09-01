import { describe, expect, it } from "vitest"
import { render, screen } from "@testing-library/react"

import { PaymentMethodBreakdown } from "@/components/payments/payment-method-breakdown"
import type { CashfreePaymentMethodBreakdown } from "@/types/cashfree"

const data: CashfreePaymentMethodBreakdown = {
  items: [
    { payment_method: "upi", count: 8, amount: "1600.00" },
    { payment_method: "card", count: 2, amount: "400.00" },
  ],
}

describe("PaymentMethodBreakdown", () => {
  it("renders each method with its count", () => {
    render(
      <PaymentMethodBreakdown data={data} isLoading={false} hrefFor={(m) => `/payments?payment_method=${m}`} />
    )
    expect(screen.getByText("Payment Method")).toBeInTheDocument()
    expect(screen.getByText("Upi")).toBeInTheDocument()
    expect(screen.getByText("Card")).toBeInTheDocument()
  })

  it("shows an empty state when there is no data", () => {
    render(
      <PaymentMethodBreakdown
        data={{ items: [] }}
        isLoading={false}
        hrefFor={(m) => `/payments?payment_method=${m}`}
      />
    )
    expect(screen.getByText("No data in the selected range.")).toBeInTheDocument()
  })
})

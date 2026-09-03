import { describe, expect, it } from "vitest"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { PaymentOverviewCards } from "@/components/payments/payment-overview-cards"
import type { CashfreePaymentOverview } from "@/types/cashfree"

const overview: CashfreePaymentOverview = {
  date_from: "2026-01-01T00:00:00Z",
  date_to: "2026-02-01T00:00:00Z",
  total_payments: { current: "10", previous: "8", change_pct: 25 },
  paid_payments: { current: "6", previous: "5", change_pct: 20 },
  pending_payments: { current: "2", previous: "2", change_pct: 0 },
  failed_payments: { current: "2", previous: "1", change_pct: 100 },
  refunded_payments: { current: "0", previous: "0", change_pct: null },
  total_amount: { current: "3000.00", previous: "2500.00", change_pct: 20 },
  pending_amount: { current: "400.00", previous: "300.00", change_pct: 33.3 },
  status_breakdown: [
    { status: "paid", count: 6 },
    { status: "pending", count: 2 },
    { status: "failed", count: 2 },
  ],
}

describe("PaymentOverviewCards", () => {
  it("renders all five KPI tiles with their values", () => {
    render(<PaymentOverviewCards data={overview} hrefFor={() => "/payments"} />)

    expect(screen.getByText("Total Payments")).toBeInTheDocument()
    expect(screen.getByText("Paid")).toBeInTheDocument()
    expect(screen.getByText("Pending")).toBeInTheDocument()
    expect(screen.getByText("Failed")).toBeInTheDocument()
    expect(screen.getByText("Paid Amount")).toBeInTheDocument()
    expect(screen.getByText("10")).toBeInTheDocument()
    expect(screen.getByText("6")).toBeInTheDocument()
  })

  it("renders a dash for every tile while data is still loading", () => {
    render(<PaymentOverviewCards data={undefined} hrefFor={() => "/payments"} />)
    expect(screen.getAllByText("—").length).toBe(5)
  })

  it("shows an error banner instead of the tiles on a genuine fetch failure", () => {
    render(
      <PaymentOverviewCards
        data={undefined}
        hrefFor={() => "/payments"}
        isError
        error={new Error("Network error")}
      />
    )
    expect(screen.getByText("Unable to load Cashfree data")).toBeInTheDocument()
    // A failed fetch must never render as if it were legitimate zero data.
    expect(screen.queryByText("Total Payments")).not.toBeInTheDocument()
    expect(screen.queryAllByText("—").length).toBe(0)
  })

  it("calls onRetry when the error banner's Retry button is clicked", async () => {
    let retried = false
    render(
      <PaymentOverviewCards
        data={undefined}
        hrefFor={() => "/payments"}
        isError
        onRetry={() => {
          retried = true
        }}
      />
    )
    await userEvent.setup().click(screen.getByRole("button", { name: "Retry" }))
    expect(retried).toBe(true)
  })
})

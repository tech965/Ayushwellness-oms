import { beforeEach, describe, expect, it, vi } from "vitest"
import { fireEvent, render, screen } from "@testing-library/react"

import { renderWithProviders } from "@/test-utils/render-with-providers"
import { LiveNdr } from "@/components/ndr/live-ndr"
import { LivePayments } from "@/components/payments/live-payments"
import { LiveModeGate } from "@/components/shared/live-mode-gate"
import { LiveUnavailable } from "@/components/shared/live-unavailable"
import {
  useLiveCapabilities,
  useLiveMode,
  useLiveNdr,
  useLiveNdrReattempt,
  useLivePaymentSummary,
  useLiveRefunds,
  useLiveSettlements,
  useLiveTransactions,
} from "@/services/live"

vi.mock("next/link", () => ({
  default: ({ href, children }: { href: string; children: React.ReactNode }) => (
    <a href={href}>{children}</a>
  ),
}))
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn() } }))
vi.mock("@/services/live", () => ({
  useLiveMode: vi.fn(),
  useLiveCapabilities: vi.fn(),
  useLivePaymentSummary: vi.fn(),
  useLiveTransactions: vi.fn(),
  useLiveRefunds: vi.fn(),
  useLiveSettlements: vi.fn(),
  useLiveNdr: vi.fn(),
  useLiveNdrReattempt: vi.fn(),
}))

const ok = <T,>(data: T) =>
  ({ data, isLoading: false, isError: false, error: null }) as never

describe("LiveModeGate", () => {
  beforeEach(() => vi.clearAllMocks())

  it("keeps the stored-data screen until live mode is confirmed", () => {
    vi.mocked(useLiveMode).mockReturnValue({ data: undefined } as never)
    render(
      <LiveModeGate live={<p>live screen</p>}>
        <p>stored screen</p>
      </LiveModeGate>
    )
    expect(screen.getByText("stored screen")).toBeInTheDocument()
    expect(screen.queryByText("live screen")).toBeNull()
  })

  it("shows the live screen in live mode", () => {
    vi.mocked(useLiveMode).mockReturnValue({ data: { live_mode: true } } as never)
    render(
      <LiveModeGate live={<p>live screen</p>}>
        <p>stored screen</p>
      </LiveModeGate>
    )
    expect(screen.getByText("live screen")).toBeInTheDocument()
    expect(screen.queryByText("stored screen")).toBeNull()
  })
})

describe("LiveUnavailable", () => {
  it("shows the server-provided reason, falling back to the supplied one", () => {
    vi.mocked(useLiveCapabilities).mockReturnValue(
      ok([{ operation: "shiprocket_aggregates", reason: "Server reason." }])
    )
    const { unmount } = renderWithProviders(
      <LiveUnavailable title="RTO" operation="shiprocket_aggregates" fallbackReason="Fallback." />
    )
    expect(screen.getByTestId("live-unavailable-reason")).toHaveTextContent("Server reason.")
    unmount()

    vi.mocked(useLiveCapabilities).mockReturnValue({ data: undefined } as never)
    renderWithProviders(
      <LiveUnavailable title="RTO" operation="shiprocket_aggregates" fallbackReason="Fallback." />
    )
    expect(screen.getByTestId("live-unavailable-reason")).toHaveTextContent("Fallback.")
  })
})

describe("LivePayments", () => {
  beforeEach(() => {
    vi.mocked(useLivePaymentSummary).mockReturnValue(
      ok({
        complete: false,
        warnings: ["Stopped after 20 Cashfree pages; results are incomplete."],
        payments_received: "499.00",
        refunded: "100.00",
        by_status: [{ status: "paid", count: 1, amount: "499.00" }],
        daily_received: [],
      })
    )
    vi.mocked(useLiveTransactions).mockReturnValue(
      ok({
        complete: true,
        warnings: [],
        items: [
          {
            event_type: "PAYMENT",
            cashfree_order_id: "1001",
            cf_payment_id: "11",
            status: "paid",
            amount: "499.00",
            currency: "INR",
            time: null,
          },
        ],
      })
    )
    vi.mocked(useLiveRefunds).mockReturnValue(ok({ complete: true, warnings: [], items: [] }))
    vi.mocked(useLiveSettlements).mockReturnValue(
      ok({
        complete: true,
        warnings: [],
        items: [],
        unsettled_amount: "500",
        unsettled_amount_is_derived: true,
      })
    )
  })

  it("shows live totals and flags a partial read instead of presenting it as complete", () => {
    renderWithProviders(<LivePayments />)
    expect(screen.getByTestId("live-payments")).toBeInTheDocument()
    expect(screen.getByTestId("live-result-warning")).toHaveTextContent(/Cashfree pages/)
    expect(screen.getAllByText(/499.00/).length).toBeGreaterThan(0)
  })

  it("says the unsettled figure is derived", () => {
    renderWithProviders(<LivePayments />)
    fireEvent.mouseDown(screen.getByRole("tab", { name: "Settlements" }))
    fireEvent.click(screen.getByRole("tab", { name: "Settlements" }))
    expect(screen.getByTestId("live-unsettled")).toHaveTextContent(/derived/)
  })

  it("shows a readable error rather than a payload when Cashfree fails", () => {
    vi.mocked(useLivePaymentSummary).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      error: new Error("Cashfree is temporarily unavailable."),
    } as never)
    renderWithProviders(<LivePayments />)
    expect(screen.getByText(/Could not load from Cashfree/)).toBeInTheDocument()
    expect(screen.getByText(/temporarily unavailable/)).toBeInTheDocument()
  })
})

describe("LiveNdr", () => {
  it("offers reattempt and an order link only for shipments this OMS knows", () => {
    vi.mocked(useLiveNdrReattempt).mockReturnValue({ mutate: vi.fn(), isPending: false } as never)
    vi.mocked(useLiveNdr).mockReturnValue(
      ok({
        has_more: false,
        next_page: null,
        items: [
          {
            awb: "AWB1",
            reason: "Customer unavailable",
            attempt_number: 1,
            courier_name: "Delhivery",
            shiprocket_order_id: "5",
            external_created_at: null,
            shipment_id: "ship-1",
            order_id: "order-1",
          },
          {
            awb: "AWB2",
            reason: "Wrong address",
            attempt_number: 2,
            courier_name: "Delhivery",
            shiprocket_order_id: "6",
            external_created_at: null,
            shipment_id: null,
            order_id: null,
          },
        ],
      })
    )
    renderWithProviders(<LiveNdr />)

    expect(screen.getByText("Open order").closest("a")).toHaveAttribute("href", "/orders/order-1")
    expect(screen.getByText("Shiprocket only")).toBeInTheDocument()
    expect(screen.getAllByRole("button", { name: "Reattempt" })).toHaveLength(1)
  })
})

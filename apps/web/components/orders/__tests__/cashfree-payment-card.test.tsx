import { describe, expect, it, vi } from "vitest"
import { screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { renderWithProviders } from "@/test-utils/render-with-providers"
import { CashfreePaymentCard } from "@/components/orders/cashfree-payment-card"
import {
  useCashfreePayment,
  useCreateCashfreeCheckout,
  useReconcileCashfreePayment,
} from "@/services/cashfree"
import { useAuth } from "@/lib/auth-context"

vi.mock("@/services/cashfree", () => ({
  useCashfreePayment: vi.fn(),
  useCreateCashfreeCheckout: vi.fn(),
  useReconcileCashfreePayment: vi.fn(),
}))

vi.mock("@/lib/auth-context", () => ({
  useAuth: vi.fn(),
}))

const mockedUsePayment = vi.mocked(useCashfreePayment)
const mockedUseCreate = vi.mocked(useCreateCashfreeCheckout)
const mockedUseReconcile = vi.mocked(useReconcileCashfreePayment)
const mockedUseAuth = vi.mocked(useAuth)

function queryResult(overrides: Record<string, unknown> = {}) {
  return {
    isLoading: false,
    isError: false,
    error: null,
    data: undefined,
    ...overrides,
  } as unknown as ReturnType<typeof useCashfreePayment>
}

function mutationResult<T>(overrides: Record<string, unknown> = {}): T {
  return { mutate: vi.fn(), isPending: false, ...overrides } as unknown as T
}

function setAuth(canCreate: boolean) {
  mockedUseAuth.mockReturnValue({
    hasPermission: (code: string) => (code === "payments.create" ? canCreate : true),
  } as unknown as ReturnType<typeof useAuth>)
}

describe("CashfreePaymentCard", () => {
  it("shows a loading state", () => {
    setAuth(true)
    mockedUsePayment.mockReturnValue(queryResult({ isLoading: true }))
    mockedUseCreate.mockReturnValue(mutationResult())
    mockedUseReconcile.mockReturnValue(mutationResult())

    renderWithProviders(<CashfreePaymentCard orderId="order-1" orderPaymentStatus="pending" />)
    expect(screen.getByText("Loading...")).toBeInTheDocument()
  })

  it("offers to collect payment when no Cashfree payment exists yet", async () => {
    setAuth(true)
    mockedUsePayment.mockReturnValue(queryResult({ data: null }))
    const createMutation = mutationResult<ReturnType<typeof useCreateCashfreeCheckout>>()
    mockedUseCreate.mockReturnValue(createMutation)
    mockedUseReconcile.mockReturnValue(mutationResult())

    renderWithProviders(<CashfreePaymentCard orderId="order-1" orderPaymentStatus="pending" />)
    expect(
      screen.getByText("No Cashfree payment has been initiated for this order yet.")
    ).toBeInTheDocument()

    const button = screen.getByRole("button", { name: "Collect Payment via Cashfree" })
    await userEvent.click(button)
    expect(createMutation.mutate).toHaveBeenCalledTimes(1)
  })

  it("hides the create button for a user without payments.create", () => {
    setAuth(false)
    mockedUsePayment.mockReturnValue(queryResult({ data: null }))
    mockedUseCreate.mockReturnValue(mutationResult())
    mockedUseReconcile.mockReturnValue(mutationResult())

    renderWithProviders(<CashfreePaymentCard orderId="order-1" orderPaymentStatus="pending" />)
    expect(
      screen.queryByRole("button", { name: "Collect Payment via Cashfree" })
    ).not.toBeInTheDocument()
  })

  it("shows resume-checkout and reconcile actions for a pending payment", () => {
    setAuth(true)
    mockedUsePayment.mockReturnValue(
      queryResult({
        data: {
          payment_id: "p1",
          order_id: "order-1",
          provider: "cashfree",
          cashfree_order_id: "AWL1001",
          payment_session_id: "session_1",
          status: "pending",
          amount: "500.00",
          currency: "INR",
          payment_method: null,
          created_at: "2026-02-01T10:00:00Z",
          updated_at: "2026-02-01T10:00:00Z",
          paid_at: null,
        },
      })
    )
    mockedUseCreate.mockReturnValue(mutationResult())
    mockedUseReconcile.mockReturnValue(mutationResult())

    renderWithProviders(<CashfreePaymentCard orderId="order-1" orderPaymentStatus="pending" />)
    expect(screen.getByRole("button", { name: "Resume Checkout" })).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Check Cashfree Status" })).toBeInTheDocument()
    expect(screen.getByText("AWL1001")).toBeInTheDocument()
  })

  it("shows a paid payment with no further action buttons", () => {
    setAuth(true)
    mockedUsePayment.mockReturnValue(
      queryResult({
        data: {
          payment_id: "p1",
          order_id: "order-1",
          provider: "cashfree",
          cashfree_order_id: "AWL1001",
          payment_session_id: null,
          status: "paid",
          amount: "500.00",
          currency: "INR",
          payment_method: "upi",
          created_at: "2026-02-01T10:00:00Z",
          updated_at: "2026-02-01T10:05:00Z",
          paid_at: "2026-02-01T10:05:00Z",
        },
      })
    )
    mockedUseCreate.mockReturnValue(mutationResult())
    mockedUseReconcile.mockReturnValue(mutationResult())

    renderWithProviders(<CashfreePaymentCard orderId="order-1" orderPaymentStatus="paid" />)
    expect(screen.queryByRole("button", { name: "Resume Checkout" })).not.toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "Check Cashfree Status" })).not.toBeInTheDocument()
    // `uppercase` is a CSS text-transform, not a DOM text change -- the
    // rendered node's actual text content stays lowercase.
    expect(screen.getByText("upi")).toBeInTheDocument()
  })

  it("shows an error message for a non-404 failure", () => {
    setAuth(true)
    mockedUsePayment.mockReturnValue(
      queryResult({ isError: true, error: new Error("Network down") })
    )
    mockedUseCreate.mockReturnValue(mutationResult())
    mockedUseReconcile.mockReturnValue(mutationResult())

    renderWithProviders(<CashfreePaymentCard orderId="order-1" orderPaymentStatus="pending" />)
    expect(screen.getByText("Network down")).toBeInTheDocument()
  })
})

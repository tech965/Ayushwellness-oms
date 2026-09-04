import { describe, expect, it, vi } from "vitest"
import { screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { renderWithProviders } from "@/test-utils/render-with-providers"
import TelecallerCheckoutDetailPage from "@/app/(dashboard)/telecaller/checkouts/[id]/page"
import {
  useCheckoutCallHistory,
  useLogCheckoutCall,
  useMyCheckout,
  useScheduleCheckoutFollowUp,
} from "@/services/telecaller"

vi.mock("next/navigation", () => ({
  useParams: () => ({ id: "checkout-1" }),
}))

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

vi.mock("@/services/telecaller", () => ({
  useMyCheckout: vi.fn(),
  useCheckoutCallHistory: vi.fn(),
  useLogCheckoutCall: vi.fn(),
  useScheduleCheckoutFollowUp: vi.fn(),
}))

const mockedUseMyCheckout = vi.mocked(useMyCheckout)
const mockedUseCheckoutCallHistory = vi.mocked(useCheckoutCallHistory)
const mockedUseLogCheckoutCall = vi.mocked(useLogCheckoutCall)
const mockedUseScheduleCheckoutFollowUp = vi.mocked(useScheduleCheckoutFollowUp)

const CHECKOUT = {
  checkout_id: "checkout-1",
  customer_name: "Priya",
  customer_phone: "9990000099",
  customer_email: "priya@example.com",
  item_summary: "Ashwagandha Capsules",
  total_amount: "899.00",
  checkout_url: "https://example.myshopify.com/checkout/abc",
  checkout_created_at: "2026-09-01T10:00:00Z",
  is_recovered: false,
  assignment_id: "cassign-1",
  assigned_to: "tc-1",
  assigned_to_name: "Telecaller One",
  call_status: "not_called",
  attempt_count: 0,
  last_attempt_at: null,
  next_follow_up_at: null,
  lead_category: "abandoned_checkout" as const,
  priority: "high" as const,
}

describe("TelecallerCheckoutDetailPage", () => {
  it("renders checkout contact info and submits a logged call", async () => {
    const user = userEvent.setup()
    const mutate = vi.fn()

    mockedUseMyCheckout.mockReturnValue({
      isLoading: false,
      isError: false,
      error: null,
      data: CHECKOUT,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useMyCheckout>)

    mockedUseCheckoutCallHistory.mockReturnValue({
      isLoading: false,
      isError: false,
      error: null,
      data: [],
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useCheckoutCallHistory>)

    mockedUseLogCheckoutCall.mockReturnValue({
      mutate,
      isPending: false,
    } as unknown as ReturnType<typeof useLogCheckoutCall>)

    mockedUseScheduleCheckoutFollowUp.mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useScheduleCheckoutFollowUp>)

    renderWithProviders(<TelecallerCheckoutDetailPage />)

    expect(screen.getByText("Priya")).toBeInTheDocument()
    expect(screen.getByText("priya@example.com")).toBeInTheDocument()

    await user.click(screen.getByRole("button", { name: /^Log Call$/i }))
    await user.type(
      screen.getByPlaceholderText("Notes (optional)"),
      "Asked for a callback tomorrow."
    )
    await user.click(screen.getByRole("button", { name: /^Save$/i }))

    expect(mutate).toHaveBeenCalledWith(
      {
        outcome: "connected",
        notes: "Asked for a callback tomorrow.",
        next_follow_up_at: undefined,
      },
      expect.anything()
    )
  }, 15000)

  it("logs a quick-status call for Mark Converted", async () => {
    const user = userEvent.setup()
    const mutate = vi.fn()

    mockedUseMyCheckout.mockReturnValue({
      isLoading: false,
      isError: false,
      error: null,
      data: CHECKOUT,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useMyCheckout>)
    mockedUseCheckoutCallHistory.mockReturnValue({
      isLoading: false,
      isError: false,
      error: null,
      data: [],
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useCheckoutCallHistory>)
    mockedUseLogCheckoutCall.mockReturnValue({
      mutate,
      isPending: false,
    } as unknown as ReturnType<typeof useLogCheckoutCall>)
    mockedUseScheduleCheckoutFollowUp.mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useScheduleCheckoutFollowUp>)

    renderWithProviders(<TelecallerCheckoutDetailPage />)

    await user.click(screen.getByRole("button", { name: /Mark Converted/i }))
    expect(mutate).toHaveBeenCalledWith({ outcome: "confirmed" }, expect.anything())
  })
})

import { describe, expect, it, vi } from "vitest"
import { screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { renderWithProviders } from "@/test-utils/render-with-providers"
import TelecallerOrderDetailPage from "@/app/(dashboard)/telecaller/orders/[id]/page"
import {
  useCallHistory,
  useLogCall,
  useMyOrder,
  useScheduleFollowUp,
} from "@/services/telecaller"

vi.mock("next/navigation", () => ({
  useParams: () => ({ id: "order-1" }),
}))

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

vi.mock("@/services/telecaller", () => ({
  useMyOrder: vi.fn(),
  useCallHistory: vi.fn(),
  useLogCall: vi.fn(),
  useScheduleFollowUp: vi.fn(),
}))

const mockedUseMyOrder = vi.mocked(useMyOrder)
const mockedUseCallHistory = vi.mocked(useCallHistory)
const mockedUseLogCall = vi.mocked(useLogCall)
const mockedUseScheduleFollowUp = vi.mocked(useScheduleFollowUp)

const ORDER = {
  order_id: "order-1",
  order_number: "OMS-0001",
  customer_name: "Alice",
  customer_phone: "9990000001",
  item_summary: "Ashwagandha",
  total_amount: "499.00",
  payment_type: "prepaid",
  payment_status: "paid",
  fulfillment_status: "unfulfilled",
  order_datetime: "2026-08-01T00:00:00Z",
  shipping_address: null,
  assignment_id: "assign-1",
  assigned_to: "tc-1",
  assigned_to_name: "Telecaller One",
  call_status: "not_received",
  attempt_count: 1,
  last_attempt_at: "2026-08-27T11:32:00Z",
  next_follow_up_at: null,
}

const CALL_HISTORY = [
  {
    id: "call-1",
    order_id: "order-1",
    telecaller_id: "tc-1",
    attempt_number: 1,
    attempted_at: "2026-08-27T11:32:00Z",
    outcome: "not_received",
    notes: null,
    next_follow_up_at: null,
    created_at: "2026-08-27T11:32:00Z",
  },
]

describe("TelecallerOrderDetailPage", () => {
  it("renders order + call management info and submits a logged call", async () => {
    const user = userEvent.setup()
    const mutate = vi.fn()

    mockedUseMyOrder.mockReturnValue({
      isLoading: false,
      isError: false,
      error: null,
      data: ORDER,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useMyOrder>)

    mockedUseCallHistory.mockReturnValue({
      isLoading: false,
      isError: false,
      error: null,
      data: CALL_HISTORY,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useCallHistory>)

    mockedUseLogCall.mockReturnValue({
      mutate,
      isPending: false,
    } as unknown as ReturnType<typeof useLogCall>)

    mockedUseScheduleFollowUp.mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useScheduleFollowUp>)

    renderWithProviders(<TelecallerOrderDetailPage />)

    expect(screen.getByText("Alice")).toBeInTheDocument()
    expect(screen.getByText("Attempt #1")).toBeInTheDocument()

    await user.click(screen.getByRole("button", { name: /^Log Call$/i }))
    await user.type(screen.getByPlaceholderText("Notes (optional)"), "Customer answered.")
    await user.click(screen.getByRole("button", { name: /^Save$/i }))

    expect(mutate).toHaveBeenCalledWith(
      { outcome: "connected", notes: "Customer answered.", next_follow_up_at: undefined },
      expect.anything()
    )
  })

  it("logs a quick-status call with no dialog for Mark Confirmed", async () => {
    const user = userEvent.setup()
    const mutate = vi.fn()

    mockedUseMyOrder.mockReturnValue({
      isLoading: false,
      isError: false,
      error: null,
      data: ORDER,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useMyOrder>)
    mockedUseCallHistory.mockReturnValue({
      isLoading: false,
      isError: false,
      error: null,
      data: CALL_HISTORY,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useCallHistory>)
    mockedUseLogCall.mockReturnValue({
      mutate,
      isPending: false,
    } as unknown as ReturnType<typeof useLogCall>)
    mockedUseScheduleFollowUp.mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useScheduleFollowUp>)

    renderWithProviders(<TelecallerOrderDetailPage />)

    await user.click(screen.getByRole("button", { name: /Mark Confirmed/i }))
    expect(mutate).toHaveBeenCalledWith({ outcome: "confirmed" }, expect.anything())
  })
})

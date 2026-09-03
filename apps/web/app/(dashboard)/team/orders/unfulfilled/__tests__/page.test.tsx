import { describe, expect, it, vi } from "vitest"
import { screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { renderWithProviders } from "@/test-utils/render-with-providers"
import UnfulfilledTeamOrdersPage from "@/app/(dashboard)/team/orders/unfulfilled/page"
import {
  useAssignableTelecallers,
  useAssignOrders,
  useTeamTelecallers,
  useUnfulfilledTeamOrders,
} from "@/services/team"

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn() }),
  usePathname: () => "/team/orders/unfulfilled",
  useSearchParams: () => new URLSearchParams(),
}))

vi.mock("@/services/team", () => ({
  useUnfulfilledTeamOrders: vi.fn(),
  useTeamTelecallers: vi.fn(),
  useAssignableTelecallers: vi.fn(),
  useAssignOrders: vi.fn(),
}))

const mockedUseUnfulfilledTeamOrders = vi.mocked(useUnfulfilledTeamOrders)
const mockedUseTeamTelecallers = vi.mocked(useTeamTelecallers)
const mockedUseAssignableTelecallers = vi.mocked(useAssignableTelecallers)
const mockedUseAssignOrders = vi.mocked(useAssignOrders)

const ORDERS = [
  {
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
    assignment_id: null,
    assigned_to: null,
    assigned_to_name: null,
    call_status: null,
    attempt_count: 0,
    last_attempt_at: null,
    next_follow_up_at: null,
  },
  {
    order_id: "order-2",
    order_number: "OMS-0002",
    customer_name: "Bob",
    customer_phone: "9990000002",
    item_summary: "A2 Milk Powder",
    total_amount: "1299.00",
    payment_type: "cod",
    payment_status: "pending",
    fulfillment_status: "unfulfilled",
    order_datetime: "2026-08-02T00:00:00Z",
    shipping_address: null,
    assignment_id: null,
    assigned_to: null,
    assigned_to_name: null,
    call_status: null,
    attempt_count: 0,
    last_attempt_at: null,
    next_follow_up_at: null,
  },
]

const TELECALLERS = [
  {
    telecaller_id: "tc-1",
    telecaller_name: "Telecaller One",
    assigned: 0,
    called: 0,
    connected: 0,
    follow_ups: 0,
    confirmed: 0,
    not_interested: 0,
  },
  {
    telecaller_id: "tc-2",
    telecaller_name: "Telecaller Two",
    assigned: 0,
    called: 0,
    connected: 0,
    follow_ups: 0,
    confirmed: 0,
    not_interested: 0,
  },
]

// The "Select Telecaller" assign-dialog roster — active TELECALLER users
// regardless of assignment history, distinct from the performance-count
// shape above (see `useAssignableTelecallers`).
const TELECALLER_ROSTER = [
  { id: "tc-1", name: "Telecaller One", email: "one@example.com" },
  { id: "tc-2", name: "Telecaller Two", email: "two@example.com" },
]

describe("UnfulfilledTeamOrdersPage", () => {
  it("disables Bulk Assign until at least one order is selected, then enables it and submits equal distribution", async () => {
    const user = userEvent.setup()
    const mutate = vi.fn()

    mockedUseUnfulfilledTeamOrders.mockReturnValue({
      isLoading: false,
      isError: false,
      error: null,
      data: {
        success: true,
        message: "Success",
        data: ORDERS,
        meta: { page: 1, page_size: 20, total_items: 2, total_pages: 1 },
      },
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useUnfulfilledTeamOrders>)

    mockedUseTeamTelecallers.mockReturnValue({
      data: TELECALLERS,
    } as unknown as ReturnType<typeof useTeamTelecallers>)

    mockedUseAssignableTelecallers.mockReturnValue({
      isLoading: false,
      isError: false,
      data: TELECALLER_ROSTER,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useAssignableTelecallers>)

    mockedUseAssignOrders.mockReturnValue({
      mutate,
      isPending: false,
    } as unknown as ReturnType<typeof useAssignOrders>)

    renderWithProviders(<UnfulfilledTeamOrdersPage />)

    const bulkAssignButton = screen.getByRole("button", { name: /^Bulk Assign$/i })
    expect(bulkAssignButton).toBeDisabled()

    // Select both orders via "select all on this page".
    const selectAllCheckbox = screen.getByRole("checkbox", {
      name: /select all rows on this page/i,
    })
    await user.click(selectAllCheckbox)

    const bulkAssignButtonSelected = screen.getByRole("button", {
      name: /Bulk Assign \(2\)/i,
    })
    expect(bulkAssignButtonSelected).toBeEnabled()

    await user.click(bulkAssignButtonSelected)
    expect(screen.getByText("Bulk Assign 2 Order(s)")).toBeInTheDocument()

    // Default mode is "equal" — check both telecallers, then submit.
    await user.click(screen.getByText("Telecaller One"))
    await user.click(screen.getByText("Telecaller Two"))
    await user.click(screen.getByRole("button", { name: /^Assign$/i }))

    expect(mutate).toHaveBeenCalledWith(
      {
        order_ids: ["order-1", "order-2"],
        mode: "equal",
        telecaller_ids: ["tc-1", "tc-2"],
      },
      expect.anything()
    )
  }, 15000)
})

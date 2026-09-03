import { describe, expect, it, vi } from "vitest"
import { screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { renderWithProviders } from "@/test-utils/render-with-providers"
import LeadPoolPage from "@/app/(dashboard)/team/leads/page"
import { useAssignOrders, useLeadPool, useTeamTelecallers } from "@/services/team"

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn() }),
  usePathname: () => "/team/leads",
  useSearchParams: () => new URLSearchParams(),
}))

vi.mock("@/services/team", () => ({
  useLeadPool: vi.fn(),
  useTeamTelecallers: vi.fn(),
  useAssignOrders: vi.fn(),
}))

const mockedUseLeadPool = vi.mocked(useLeadPool)
const mockedUseTeamTelecallers = vi.mocked(useTeamTelecallers)
const mockedUseAssignOrders = vi.mocked(useAssignOrders)

const LEADS = [
  {
    order_id: "order-1",
    order_number: "OMS-0001",
    customer_name: "Alice",
    customer_phone: "9990000001",
    item_summary: "Ashwagandha",
    total_amount: "499.00",
    payment_type: "cod",
    payment_status: "pending",
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
    lead_category: "cod_unfulfilled",
    priority: "high",
  },
]

const TELECALLERS = [
  {
    telecaller_id: "tc-1",
    telecaller_name: "Telecaller One",
    assigned: 0,
    called: 0,
    pending: 0,
    connected: 0,
    interested: 0,
    follow_ups: 0,
    confirmed: 0,
    not_interested: 0,
    conversion_rate: 0,
  },
]

describe("LeadPoolPage", () => {
  it("shows category tabs and assigns a selected COD Unfulfilled lead", async () => {
    const user = userEvent.setup()
    const mutate = vi.fn()

    mockedUseLeadPool.mockReturnValue({
      isLoading: false,
      isError: false,
      error: null,
      data: {
        success: true,
        message: "Success",
        data: LEADS,
        meta: { page: 1, page_size: 20, total_items: 1, total_pages: 1 },
      },
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useLeadPool>)

    mockedUseTeamTelecallers.mockReturnValue({
      data: TELECALLERS,
    } as unknown as ReturnType<typeof useTeamTelecallers>)

    mockedUseAssignOrders.mockReturnValue({
      mutate,
      isPending: false,
    } as unknown as ReturnType<typeof useAssignOrders>)

    renderWithProviders(<LeadPoolPage />)

    expect(screen.getByRole("tab", { name: "COD Unfulfilled" })).toBeInTheDocument()
    expect(screen.getByRole("tab", { name: "COD Fulfilled" })).toBeInTheDocument()
    expect(screen.getByRole("tab", { name: "Prepaid" })).toBeInTheDocument()

    const selectAllCheckbox = screen.getByRole("checkbox", {
      name: /select all rows on this page/i,
    })
    await user.click(selectAllCheckbox)

    await user.click(screen.getByRole("button", { name: /Bulk Assign \(1\)/i }))
    expect(screen.getByText("Bulk Assign 1 Lead(s)")).toBeInTheDocument()

    // Default mode is "equal" — check the one telecaller, then submit.
    await user.click(screen.getByText("Telecaller One"))
    await user.click(screen.getByRole("button", { name: /^Assign$/i }))

    expect(mutate).toHaveBeenCalledWith(
      { order_ids: ["order-1"], mode: "equal", telecaller_ids: ["tc-1"] },
      expect.anything()
    )
  })
})

import { describe, expect, it, vi } from "vitest"
import { screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { renderWithProviders } from "@/test-utils/render-with-providers"
import LeadPoolPage from "@/app/(dashboard)/team/leads/page"
import { useAssignableTelecallers, useAssignOrders, useLeadPool } from "@/services/team"

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn() }),
  usePathname: () => "/team/leads",
  useSearchParams: () => new URLSearchParams(),
}))

vi.mock("@/services/team", () => ({
  useLeadPool: vi.fn(),
  useAssignableTelecallers: vi.fn(),
  useAssignOrders: vi.fn(),
}))

const mockedUseLeadPool = vi.mocked(useLeadPool)
const mockedUseAssignableTelecallers = vi.mocked(useAssignableTelecallers)
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

// A brand-new telecaller with zero assignment history -- exactly the
// case the old (`useTeamTelecallers`-backed) dropdown used to miss.
const TELECALLER_ROSTER = [
  { id: "tc-1", name: "Telecaller One", email: "one@example.com" },
]

function mockLeadPool() {
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
}

async function openAssignDialog(user: ReturnType<typeof userEvent.setup>) {
  const selectAllCheckbox = screen.getByRole("checkbox", {
    name: /select all rows on this page/i,
  })
  await user.click(selectAllCheckbox)
  await user.click(screen.getByRole("button", { name: /Bulk Assign \(1\)/i }))
}

describe("LeadPoolPage bulk assignment", () => {
  it("shows category tabs and assigns a selected COD Unfulfilled lead to an existing Telecaller", async () => {
    const user = userEvent.setup()
    const mutate = vi.fn()

    mockLeadPool()
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

    renderWithProviders(<LeadPoolPage />)

    expect(screen.getByRole("tab", { name: "COD Unfulfilled" })).toBeInTheDocument()
    expect(screen.getByRole("tab", { name: "COD Fulfilled" })).toBeInTheDocument()
    expect(screen.getByRole("tab", { name: "Prepaid" })).toBeInTheDocument()

    await openAssignDialog(user)
    expect(screen.getByText("Bulk Assign 1 Lead(s)")).toBeInTheDocument()

    // Default mode is "equal" — the roster-sourced telecaller (not the
    // old performance-count source) is what's checked here.
    await user.click(screen.getByText("Telecaller One"))
    await user.click(screen.getByRole("button", { name: /^Assign$/i }))

    expect(mutate).toHaveBeenCalledWith(
      { order_ids: ["order-1"], mode: "equal", telecaller_ids: ["tc-1"] },
      expect.anything()
    )
  }, 15000)

  it("selecting Manual enables the telecaller selector and Assign only once a telecaller is chosen", async () => {
    const user = userEvent.setup()
    const mutate = vi.fn()

    mockLeadPool()
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

    renderWithProviders(<LeadPoolPage />)
    await openAssignDialog(user)

    // Only the mode selector is rendered while still in "equal" mode.
    await user.click(screen.getByRole("combobox"))
    await user.click(screen.getByRole("option", { name: "Manual (one telecaller)" }))

    const assignButton = screen.getByRole("button", { name: /^Assign$/i })
    expect(assignButton).toBeDisabled()

    // Two comboboxes now: the mode selector (already switched to
    // "Manual") and this one, the telecaller selector.
    const comboboxes = screen.getAllByRole("combobox")
    await user.click(comboboxes[comboboxes.length - 1])
    await user.click(screen.getByRole("option", { name: "Telecaller One" }))

    expect(assignButton).toBeEnabled()
    await user.click(assignButton)

    expect(mutate).toHaveBeenCalledWith(
      { order_ids: ["order-1"], mode: "manual", telecaller_id: "tc-1" },
      expect.anything()
    )
  }, 15000)

  it("shows a loading state while telecallers are being fetched", async () => {
    const user = userEvent.setup()

    mockLeadPool()
    mockedUseAssignableTelecallers.mockReturnValue({
      isLoading: true,
      isError: false,
      data: undefined,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useAssignableTelecallers>)
    mockedUseAssignOrders.mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useAssignOrders>)

    renderWithProviders(<LeadPoolPage />)
    await openAssignDialog(user)

    expect(screen.getByText("Loading telecallers...")).toBeInTheDocument()
  })

  it("shows an error with Retry when the telecaller roster fails to load, and never a silently empty dropdown", async () => {
    const user = userEvent.setup()
    const refetch = vi.fn()

    mockLeadPool()
    mockedUseAssignableTelecallers.mockReturnValue({
      isLoading: false,
      isError: true,
      data: undefined,
      refetch,
    } as unknown as ReturnType<typeof useAssignableTelecallers>)
    mockedUseAssignOrders.mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useAssignOrders>)

    renderWithProviders(<LeadPoolPage />)
    await openAssignDialog(user)

    expect(screen.getByText("Could not load telecallers.")).toBeInTheDocument()
    expect(
      screen.queryByRole("combobox", { name: /select telecaller/i })
    ).not.toBeInTheDocument()

    await user.click(screen.getByRole("button", { name: /^Retry$/i }))
    expect(refetch).toHaveBeenCalled()
  })

  it("shows a useful message when there are genuinely no active telecallers", async () => {
    const user = userEvent.setup()

    mockLeadPool()
    mockedUseAssignableTelecallers.mockReturnValue({
      isLoading: false,
      isError: false,
      data: [],
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useAssignableTelecallers>)
    mockedUseAssignOrders.mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useAssignOrders>)

    renderWithProviders(<LeadPoolPage />)
    await openAssignDialog(user)

    expect(screen.getByText("No active telecallers found.")).toBeInTheDocument()
  })
})

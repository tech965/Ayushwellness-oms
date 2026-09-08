import { describe, expect, it, vi } from "vitest"
import { screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { renderWithProviders } from "@/test-utils/render-with-providers"
import TelecallerOrdersPage from "@/app/(dashboard)/telecaller/orders/page"
import {
  useBulkConfirmOrders,
  useCallHistory,
  useConfirmOrder,
  useLogCall,
  useMyOrders,
  useScheduleFollowUp,
  useUnconfirmOrder,
} from "@/services/telecaller"

const mockPush = vi.fn()

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockPush, replace: vi.fn(), back: vi.fn() }),
  usePathname: () => "/telecaller/orders",
  useSearchParams: () => new URLSearchParams(),
}))

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn() },
}))

vi.mock("@/services/telecaller", () => ({
  useMyOrders: vi.fn(),
  useLogCall: vi.fn(),
  useScheduleFollowUp: vi.fn(),
  useCallHistory: vi.fn(),
  useBulkConfirmOrders: vi.fn(),
  useConfirmOrder: vi.fn(),
  useUnconfirmOrder: vi.fn(),
}))

const mockedUseMyOrders = vi.mocked(useMyOrders)
const mockedUseLogCall = vi.mocked(useLogCall)
const mockedUseScheduleFollowUp = vi.mocked(useScheduleFollowUp)
const mockedUseCallHistory = vi.mocked(useCallHistory)
const mockedUseBulkConfirmOrders = vi.mocked(useBulkConfirmOrders)
const mockedUseConfirmOrder = vi.mocked(useConfirmOrder)
const mockedUseUnconfirmOrder = vi.mocked(useUnconfirmOrder)

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
    status: "pending",
    fulfillment_status: "unfulfilled",
    confirmed_at: null,
    confirmed_by_telecaller_id: null,
    order_datetime: "2026-08-01T00:00:00Z",
    shipping_address: null,
    assignment_id: "assign-1",
    assigned_to: "tc-1",
    assigned_to_name: "Telecaller One",
    call_status: "not_received",
    attempt_count: 1,
    last_attempt_at: "2026-08-27T11:32:00Z",
    next_follow_up_at: null,
    lead_category: null,
    priority: null,
  },
]

function mockCommonHooks() {
  mockedUseLogCall.mockReturnValue({
    mutate: vi.fn(),
    isPending: false,
  } as unknown as ReturnType<typeof useLogCall>)
  mockedUseConfirmOrder.mockReturnValue({
    mutate: vi.fn(),
    isPending: false,
  } as unknown as ReturnType<typeof useConfirmOrder>)
  mockedUseUnconfirmOrder.mockReturnValue({
    mutate: vi.fn(),
    isPending: false,
  } as unknown as ReturnType<typeof useUnconfirmOrder>)
  mockedUseScheduleFollowUp.mockReturnValue({
    mutate: vi.fn(),
    isPending: false,
  } as unknown as ReturnType<typeof useScheduleFollowUp>)
  mockedUseCallHistory.mockReturnValue({
    isLoading: false,
    isError: false,
    error: null,
    data: [],
    refetch: vi.fn(),
  } as unknown as ReturnType<typeof useCallHistory>)
}

describe("TelecallerOrdersPage", () => {
  it("renders assigned orders with a quick-edit action menu per row", async () => {
    const user = userEvent.setup()
    mockedUseMyOrders.mockReturnValue({
      isLoading: false,
      isError: false,
      error: null,
      data: {
        data: ORDERS,
        meta: { page: 1, page_size: 20, total_items: 1, total_pages: 1 },
      },
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useMyOrders>)
    mockCommonHooks()
    mockedUseBulkConfirmOrders.mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useBulkConfirmOrders>)

    renderWithProviders(<TelecallerOrdersPage />)

    expect(screen.getByText("Alice")).toBeInTheDocument()
    await user.click(screen.getByRole("button", { name: /Order actions/i }))
    expect(screen.getByText("Edit Call Status")).toBeInTheDocument()
    expect(screen.getByText("Edit Follow-up")).toBeInTheDocument()
    expect(screen.getByText("View Call History")).toBeInTheDocument()
    expect(screen.getByText("View Order")).toBeInTheDocument()
  })

  it("edits call status from the row menu without navigating away", async () => {
    const user = userEvent.setup()
    const mutate = vi.fn()
    mockedUseMyOrders.mockReturnValue({
      isLoading: false,
      isError: false,
      error: null,
      data: {
        data: ORDERS,
        meta: { page: 1, page_size: 20, total_items: 1, total_pages: 1 },
      },
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useMyOrders>)
    mockCommonHooks()
    mockedUseLogCall.mockReturnValue({
      mutate,
      isPending: false,
    } as unknown as ReturnType<typeof useLogCall>)
    mockedUseBulkConfirmOrders.mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useBulkConfirmOrders>)

    renderWithProviders(<TelecallerOrdersPage />)

    await user.click(screen.getByRole("button", { name: /Order actions/i }))
    await user.click(screen.getByText("Edit Call Status"))
    expect(screen.getByText("Edit Call Status — OMS-0001")).toBeInTheDocument()

    await user.click(screen.getByRole("button", { name: /^Save$/i }))
    expect(mutate).toHaveBeenCalledWith(
      { outcome: "not_received", notes: undefined },
      expect.anything()
    )
    expect(mockPush).not.toHaveBeenCalled()
  })

  it("selects orders and bulk confirms them, reporting per-order results", async () => {
    const user = userEvent.setup()
    const bulkMutate = vi.fn((_ids, opts) => {
      opts.onSuccess({
        confirmed_count: 1,
        failed_count: 0,
        results: [{ order_id: "order-1", success: true, message: null }],
      })
    })
    mockedUseMyOrders.mockReturnValue({
      isLoading: false,
      isError: false,
      error: null,
      data: {
        data: ORDERS,
        meta: { page: 1, page_size: 20, total_items: 1, total_pages: 1 },
      },
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useMyOrders>)
    mockCommonHooks()
    mockedUseBulkConfirmOrders.mockReturnValue({
      mutate: bulkMutate,
      isPending: false,
    } as unknown as ReturnType<typeof useBulkConfirmOrders>)

    renderWithProviders(<TelecallerOrdersPage />)

    const checkboxes = screen.getAllByRole("checkbox")
    // First checkbox is "select all"; the row's own checkbox is next.
    await user.click(checkboxes[1])

    expect(screen.getByText("1 orders selected")).toBeInTheDocument()
    await user.click(screen.getByRole("button", { name: /Bulk Confirm \(1\)/i }))

    const dialog = screen.getByRole("alertdialog")
    expect(within(dialog).getByText("Confirm 1 selected orders?")).toBeInTheDocument()
    await user.click(within(dialog).getByRole("button", { name: /^Confirm Orders$/i }))

    expect(bulkMutate).toHaveBeenCalledWith(["order-1"], expect.anything())
  })

  it("does not show the bulk confirm button when nothing is selected", () => {
    mockedUseMyOrders.mockReturnValue({
      isLoading: false,
      isError: false,
      error: null,
      data: {
        data: ORDERS,
        meta: { page: 1, page_size: 20, total_items: 1, total_pages: 1 },
      },
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useMyOrders>)
    mockCommonHooks()
    mockedUseBulkConfirmOrders.mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useBulkConfirmOrders>)

    renderWithProviders(<TelecallerOrdersPage />)

    expect(screen.queryByRole("button", { name: /Bulk Confirm/i })).not.toBeInTheDocument()
  })

  it("shows Confirm Order for a pending order and calls the single-order confirm action", async () => {
    const user = userEvent.setup()
    const confirmMutate = vi.fn()
    mockedUseMyOrders.mockReturnValue({
      isLoading: false,
      isError: false,
      error: null,
      data: {
        data: ORDERS,
        meta: { page: 1, page_size: 20, total_items: 1, total_pages: 1 },
      },
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useMyOrders>)
    mockCommonHooks()
    mockedUseConfirmOrder.mockReturnValue({
      mutate: confirmMutate,
      isPending: false,
    } as unknown as ReturnType<typeof useConfirmOrder>)
    mockedUseBulkConfirmOrders.mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useBulkConfirmOrders>)

    renderWithProviders(<TelecallerOrdersPage />)

    await user.click(screen.getByRole("button", { name: /Order actions/i }))
    await user.click(screen.getByText("Confirm Order"))

    const dialog = screen.getByRole("alertdialog")
    expect(within(dialog).getByText("Confirm order OMS-0001?")).toBeInTheDocument()
    await user.click(within(dialog).getByRole("button", { name: /^Confirm Order$/i }))

    expect(confirmMutate).toHaveBeenCalledWith(undefined, expect.anything())
  })

  it("does not show Confirm Order for an order that is already confirmed", async () => {
    const user = userEvent.setup()
    mockedUseMyOrders.mockReturnValue({
      isLoading: false,
      isError: false,
      error: null,
      data: {
        data: [{ ...ORDERS[0], status: "confirmed" }],
        meta: { page: 1, page_size: 20, total_items: 1, total_pages: 1 },
      },
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useMyOrders>)
    mockCommonHooks()
    mockedUseBulkConfirmOrders.mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useBulkConfirmOrders>)

    renderWithProviders(<TelecallerOrdersPage />)

    await user.click(screen.getByRole("button", { name: /Order actions/i }))
    expect(screen.queryByText("Confirm Order")).not.toBeInTheDocument()
  })

  it("shows Revert to Pending for a confirmed order and calls the unconfirm action", async () => {
    const user = userEvent.setup()
    const unconfirmMutate = vi.fn()
    mockedUseMyOrders.mockReturnValue({
      isLoading: false,
      isError: false,
      error: null,
      data: {
        data: [{ ...ORDERS[0], status: "confirmed" }],
        meta: { page: 1, page_size: 20, total_items: 1, total_pages: 1 },
      },
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useMyOrders>)
    mockCommonHooks()
    mockedUseUnconfirmOrder.mockReturnValue({
      mutate: unconfirmMutate,
      isPending: false,
    } as unknown as ReturnType<typeof useUnconfirmOrder>)
    mockedUseBulkConfirmOrders.mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useBulkConfirmOrders>)

    renderWithProviders(<TelecallerOrdersPage />)

    await user.click(screen.getByRole("button", { name: /Order actions/i }))
    await user.click(screen.getByText("Revert to Pending"))

    const dialog = screen.getByRole("alertdialog")
    expect(within(dialog).getByText("Revert order OMS-0001 to Pending?")).toBeInTheDocument()
    await user.click(within(dialog).getByRole("button", { name: /^Revert Order$/i }))

    expect(unconfirmMutate).toHaveBeenCalledWith(undefined, expect.anything())
  })

  it("does not show Revert to Pending for a pending order", async () => {
    const user = userEvent.setup()
    mockedUseMyOrders.mockReturnValue({
      isLoading: false,
      isError: false,
      error: null,
      data: {
        data: ORDERS,
        meta: { page: 1, page_size: 20, total_items: 1, total_pages: 1 },
      },
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useMyOrders>)
    mockCommonHooks()
    mockedUseBulkConfirmOrders.mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useBulkConfirmOrders>)

    renderWithProviders(<TelecallerOrdersPage />)

    await user.click(screen.getByRole("button", { name: /Order actions/i }))
    expect(screen.queryByText("Revert to Pending")).not.toBeInTheDocument()
  })
})

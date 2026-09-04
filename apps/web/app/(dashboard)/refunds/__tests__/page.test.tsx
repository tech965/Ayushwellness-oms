import { describe, expect, it, vi } from "vitest"
import { screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { renderWithProviders } from "@/test-utils/render-with-providers"
import RefundsPage from "@/app/(dashboard)/refunds/page"
import { useRefunds } from "@/services/refunds"
import type { Refund } from "@/types/refund"

const mockPush = vi.fn()
let mockSearchParams = new URLSearchParams()

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockPush, replace: vi.fn(), back: vi.fn() }),
  usePathname: () => "/refunds",
  useSearchParams: () => mockSearchParams,
}))

vi.mock("@/services/refunds", () => ({
  useRefunds: vi.fn(),
}))

const mockedUseRefunds = vi.mocked(useRefunds)

function queryResult(overrides: Record<string, unknown> = {}) {
  return {
    isLoading: false,
    isError: false,
    error: null,
    data: undefined,
    isFetching: false,
    refetch: vi.fn(),
    ...overrides,
  } as unknown as ReturnType<typeof useRefunds>
}

const REFUND_ROW: Refund = {
  id: "refund-1",
  order_id: "order-1",
  payment_id: null,
  return_id: "return-1",
  amount: "300.00",
  reason: "Damaged item",
  status: "pending",
  initiated_at: "2026-08-01T00:00:00Z",
  completed_at: null,
  source_system: "manual",
  created_at: "2026-08-01T00:00:00Z",
  updated_at: "2026-08-01T00:00:00Z",
  order_number: "OMS-REF-1",
  customer_name: "Ananya Rao",
  customer_phone: "9998887776",
  product: "Ashwagandha 60ct",
  order_amount: "649.00",
  payment_type: "prepaid",
}

function listData(rows: Refund[]) {
  return {
    success: true,
    message: "Success",
    data: rows,
    meta: { page: 1, page_size: 20, total_items: rows.length, total_pages: 1 },
  }
}

describe("RefundsPage", () => {
  it("renders real refund rows with enriched order/customer/product data", () => {
    mockedUseRefunds.mockReturnValue(queryResult({ data: listData([REFUND_ROW]) }))

    renderWithProviders(<RefundsPage />)

    expect(screen.getByText("OMS-REF-1")).toBeInTheDocument()
    expect(screen.getByText("Ananya Rao")).toBeInTheDocument()
    expect(screen.getByText("9998887776")).toBeInTheDocument()
    expect(screen.getByText("Ashwagandha 60ct")).toBeInTheDocument()
    expect(screen.getByText("Damaged item")).toBeInTheDocument()
    // Order Amount (₹649.00) and Refund Amount (₹300.00) both render,
    // never conflated into a single figure.
    expect(screen.getByText(/649\.00/)).toBeInTheDocument()
    expect(screen.getByText(/300\.00/)).toBeInTheDocument()
  })

  it("shows loading skeletons while fetching", () => {
    mockedUseRefunds.mockReturnValue(queryResult({ isLoading: true }))
    const { container } = renderWithProviders(<RefundsPage />)
    expect(container.querySelectorAll("[data-slot='skeleton']").length).toBeGreaterThan(0)
  })

  it("shows an error state with retry", () => {
    mockedUseRefunds.mockReturnValue(
      queryResult({ isError: true, error: new Error("Network down") })
    )
    renderWithProviders(<RefundsPage />)
    expect(screen.getByText("Network down")).toBeInTheDocument()
  })

  it("shows an empty state when there are no refunds", () => {
    mockedUseRefunds.mockReturnValue(queryResult({ data: listData([]) }))
    renderWithProviders(<RefundsPage />)
    expect(screen.getByText("No refunds found")).toBeInTheDocument()
  })

  it("navigates to the order detail page when a row is clicked", async () => {
    mockedUseRefunds.mockReturnValue(queryResult({ data: listData([REFUND_ROW]) }))
    renderWithProviders(<RefundsPage />)
    await userEvent.click(screen.getByText("OMS-REF-1"))
    expect(mockPush).toHaveBeenCalledWith("/orders/order-1")
  })

  it("reads status=completed from the URL and requests it from the API", () => {
    mockSearchParams = new URLSearchParams("status=completed")
    mockedUseRefunds.mockReturnValue(queryResult({ data: listData([]) }))

    renderWithProviders(<RefundsPage />)

    expect(mockedUseRefunds).toHaveBeenCalledWith(
      expect.objectContaining({ status: "completed" })
    )
    mockSearchParams = new URLSearchParams()
  })
})

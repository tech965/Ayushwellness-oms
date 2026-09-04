import { describe, expect, it, vi } from "vitest"
import { screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { renderWithProviders } from "@/test-utils/render-with-providers"
import ReturnsPage from "@/app/(dashboard)/returns/page"
import { useReturns, useUpdateReturn } from "@/services/returns"
import { useAuth } from "@/lib/auth-context"
import type { Return } from "@/types/return"

const mockPush = vi.fn()
let mockSearchParams = new URLSearchParams()

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockPush, replace: vi.fn(), back: vi.fn() }),
  usePathname: () => "/returns",
  useSearchParams: () => mockSearchParams,
}))

vi.mock("@/services/returns", () => ({
  useReturns: vi.fn(),
  useUpdateReturn: vi.fn(),
}))

vi.mock("@/lib/auth-context", () => ({
  useAuth: vi.fn(),
}))

const mockedUseReturns = vi.mocked(useReturns)
const mockedUseUpdateReturn = vi.mocked(useUpdateReturn)
const mockedUseAuth = vi.mocked(useAuth)

function queryResult(overrides: Record<string, unknown> = {}) {
  return {
    isLoading: false,
    isError: false,
    error: null,
    data: undefined,
    isFetching: false,
    refetch: vi.fn(),
    ...overrides,
  } as unknown as ReturnType<typeof useReturns>
}

function setAuth(canUpdate: boolean) {
  mockedUseAuth.mockReturnValue({
    hasPermission: (code: string) => (code === "returns.update" ? canUpdate : true),
  } as unknown as ReturnType<typeof useAuth>)
}

const RETURN_ROW: Return = {
  id: "return-1",
  order_id: "order-1",
  order_item_id: "item-1",
  customer_id: "customer-1",
  reason: "Damaged",
  status: "requested",
  quantity: 1,
  requested_at: "2026-08-01T00:00:00Z",
  approved_at: null,
  received_at: null,
  completed_at: null,
  notes: null,
  source_system: "manual",
  created_at: "2026-08-01T00:00:00Z",
  updated_at: "2026-08-01T00:00:00Z",
  order_number: "OMS-RET-1",
  customer_name: "Ananya Rao",
  customer_phone: "9998887776",
  product: "Ashwagandha 60ct",
  order_amount: "649.00",
  payment_type: "prepaid",
}

function listData(rows: Return[]) {
  return {
    success: true,
    message: "Success",
    data: rows,
    meta: { page: 1, page_size: 20, total_items: rows.length, total_pages: 1 },
  }
}

describe("ReturnsPage", () => {
  it("renders real return rows with enriched order/customer/product data", () => {
    setAuth(true)
    mockedUseReturns.mockReturnValue(queryResult({ data: listData([RETURN_ROW]) }))
    mockedUseUpdateReturn.mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useUpdateReturn>)

    renderWithProviders(<ReturnsPage />)

    expect(screen.getByText("OMS-RET-1")).toBeInTheDocument()
    expect(screen.getByText("Ananya Rao")).toBeInTheDocument()
    expect(screen.getByText("9998887776")).toBeInTheDocument()
    expect(screen.getByText("Ashwagandha 60ct")).toBeInTheDocument()
    expect(screen.getByText("Damaged")).toBeInTheDocument()
  })

  it("shows loading skeletons while fetching", () => {
    setAuth(true)
    mockedUseReturns.mockReturnValue(queryResult({ isLoading: true }))
    mockedUseUpdateReturn.mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useUpdateReturn>)

    const { container } = renderWithProviders(<ReturnsPage />)
    expect(container.querySelectorAll("[data-slot='skeleton']").length).toBeGreaterThan(0)
  })

  it("shows an error state with retry", () => {
    setAuth(true)
    mockedUseReturns.mockReturnValue(
      queryResult({ isError: true, error: new Error("Network down") })
    )
    mockedUseUpdateReturn.mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useUpdateReturn>)

    renderWithProviders(<ReturnsPage />)
    expect(screen.getByText("Network down")).toBeInTheDocument()
  })

  it("shows an empty state when there are no returns", () => {
    setAuth(true)
    mockedUseReturns.mockReturnValue(queryResult({ data: listData([]) }))
    mockedUseUpdateReturn.mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useUpdateReturn>)

    renderWithProviders(<ReturnsPage />)
    expect(screen.getByText("No returns found")).toBeInTheDocument()
  })

  it("navigates to the order detail page when a row is clicked", async () => {
    setAuth(true)
    mockedUseReturns.mockReturnValue(queryResult({ data: listData([RETURN_ROW]) }))
    mockedUseUpdateReturn.mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useUpdateReturn>)

    renderWithProviders(<ReturnsPage />)
    await userEvent.click(screen.getByText("OMS-RET-1"))
    expect(mockPush).toHaveBeenCalledWith("/orders/order-1")
  })

  it("reads status=completed from the URL and requests it from the API", () => {
    mockSearchParams = new URLSearchParams("status=completed")
    setAuth(true)
    mockedUseReturns.mockReturnValue(queryResult({ data: listData([]) }))
    mockedUseUpdateReturn.mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useUpdateReturn>)

    renderWithProviders(<ReturnsPage />)

    expect(mockedUseReturns).toHaveBeenCalledWith(
      expect.objectContaining({ status: "completed" })
    )
    mockSearchParams = new URLSearchParams()
  })
})

import { describe, expect, it, vi } from "vitest"
import { screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { renderWithProviders } from "@/test-utils/render-with-providers"
import RtoPage from "@/app/(dashboard)/rto/page"
import { useRtos, useUpdateRto } from "@/services/rto"
import { useAuth } from "@/lib/auth-context"
import type { RTO } from "@/types/rto"

const mockPush = vi.fn()
let mockSearchParams = new URLSearchParams()

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockPush, replace: vi.fn(), back: vi.fn() }),
  usePathname: () => "/rto",
  useSearchParams: () => mockSearchParams,
}))

vi.mock("@/services/rto", () => ({
  useRtos: vi.fn(),
  useUpdateRto: vi.fn(),
}))

vi.mock("@/lib/auth-context", () => ({
  useAuth: vi.fn(),
}))

const mockedUseRtos = vi.mocked(useRtos)
const mockedUseUpdateRto = vi.mocked(useUpdateRto)
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
  } as unknown as ReturnType<typeof useRtos>
}

function setAuth(canUpdate: boolean) {
  mockedUseAuth.mockReturnValue({
    hasPermission: (code: string) => (code === "rto.update" ? canUpdate : true),
  } as unknown as ReturnType<typeof useAuth>)
}

const RTO_ROW: RTO = {
  id: "rto-1",
  shipment_id: "shipment-1",
  order_id: "order-1",
  courier_id: null,
  reason: "Refused by customer",
  normalized_reason: null,
  external_reason: null,
  status: "initiated",
  initiated_at: "2026-08-01T00:00:00Z",
  completed_at: null,
  notes: null,
  source_system: "shiprocket",
  created_at: "2026-08-01T00:00:00Z",
  updated_at: "2026-08-01T00:00:00Z",
  order_number: "OMS-RTO-1",
  customer_name: "Ananya Rao",
  customer_phone: "9998887776",
  product: "Ashwagandha 60ct",
  order_amount: "649.00",
  payment_type: "prepaid",
  shipment_status: "rto_initiated",
}

function listData(rows: RTO[]) {
  return {
    success: true,
    message: "Success",
    data: rows,
    meta: { page: 1, page_size: 20, total_items: rows.length, total_pages: 1 },
  }
}

describe("RtoPage", () => {
  it("renders real RTO rows with enriched order/customer/product data", () => {
    setAuth(true)
    mockedUseRtos.mockReturnValue(queryResult({ data: listData([RTO_ROW]) }))
    mockedUseUpdateRto.mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useUpdateRto>)

    renderWithProviders(<RtoPage />)

    expect(screen.getByText("OMS-RTO-1")).toBeInTheDocument()
    expect(screen.getByText("Ananya Rao")).toBeInTheDocument()
    expect(screen.getByText("9998887776")).toBeInTheDocument()
    expect(screen.getByText("Ashwagandha 60ct")).toBeInTheDocument()
    expect(screen.getByText("Refused by customer")).toBeInTheDocument()
  })

  it("shows loading skeletons while fetching", () => {
    setAuth(true)
    mockedUseRtos.mockReturnValue(queryResult({ isLoading: true }))
    mockedUseUpdateRto.mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useUpdateRto>)

    const { container } = renderWithProviders(<RtoPage />)
    expect(container.querySelectorAll("[data-slot='skeleton']").length).toBeGreaterThan(0)
  })

  it("shows an error state with retry", () => {
    setAuth(true)
    mockedUseRtos.mockReturnValue(
      queryResult({ isError: true, error: new Error("Network down") })
    )
    mockedUseUpdateRto.mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useUpdateRto>)

    renderWithProviders(<RtoPage />)
    expect(screen.getByText("Network down")).toBeInTheDocument()
  })

  it("shows an empty state when there are no RTO records", () => {
    setAuth(true)
    mockedUseRtos.mockReturnValue(queryResult({ data: listData([]) }))
    mockedUseUpdateRto.mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useUpdateRto>)

    renderWithProviders(<RtoPage />)
    expect(screen.getByText("No RTO records")).toBeInTheDocument()
  })

  it("navigates to the order detail page when a row is clicked", async () => {
    setAuth(true)
    mockedUseRtos.mockReturnValue(queryResult({ data: listData([RTO_ROW]) }))
    mockedUseUpdateRto.mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useUpdateRto>)

    renderWithProviders(<RtoPage />)
    await userEvent.click(screen.getByText("OMS-RTO-1"))
    expect(mockPush).toHaveBeenCalledWith("/orders/order-1")
  })

  it("reads status=received from the URL and requests it from the API", () => {
    mockSearchParams = new URLSearchParams("status=received")
    setAuth(true)
    mockedUseRtos.mockReturnValue(queryResult({ data: listData([]) }))
    mockedUseUpdateRto.mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useUpdateRto>)

    renderWithProviders(<RtoPage />)

    expect(mockedUseRtos).toHaveBeenCalledWith(
      expect.objectContaining({ status: "received" })
    )
    mockSearchParams = new URLSearchParams()
  })
})

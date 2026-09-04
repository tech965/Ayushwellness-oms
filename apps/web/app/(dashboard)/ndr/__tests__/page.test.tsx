import { describe, expect, it, vi } from "vitest"
import { screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { renderWithProviders } from "@/test-utils/render-with-providers"
import NdrPage from "@/app/(dashboard)/ndr/page"
import { useNdrReattempt, useNdrs, useUpdateNdr } from "@/services/ndr"
import { useAuth } from "@/lib/auth-context"
import type { NDR } from "@/types/ndr"

const mockPush = vi.fn()
let mockSearchParams = new URLSearchParams()

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockPush, replace: vi.fn(), back: vi.fn() }),
  usePathname: () => "/ndr",
  useSearchParams: () => mockSearchParams,
}))

vi.mock("@/services/ndr", () => ({
  useNdrs: vi.fn(),
  useUpdateNdr: vi.fn(),
  useNdrReattempt: vi.fn(),
}))

vi.mock("@/lib/auth-context", () => ({
  useAuth: vi.fn(),
}))

const mockedUseNdrs = vi.mocked(useNdrs)
const mockedUseUpdateNdr = vi.mocked(useUpdateNdr)
const mockedUseNdrReattempt = vi.mocked(useNdrReattempt)
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
  } as unknown as ReturnType<typeof useNdrs>
}

function setAuth(canUpdate: boolean) {
  mockedUseAuth.mockReturnValue({
    hasPermission: (code: string) => (code === "ndr.update" ? canUpdate : true),
  } as unknown as ReturnType<typeof useAuth>)
}

const NDR_ROW: NDR = {
  id: "ndr-1",
  shipment_id: "shipment-1",
  order_id: "order-1",
  courier_id: null,
  reason: "Customer unavailable",
  normalized_reason: null,
  external_reason: null,
  attempt_number: 2,
  status: "open",
  customer_response: null,
  reattempt_status: null,
  reattempt_date: null,
  notes: null,
  source_system: "shiprocket",
  created_at: "2026-08-01T00:00:00Z",
  updated_at: "2026-08-01T00:00:00Z",
  order_number: "OMS-NDR-1",
  customer_name: "Ananya Rao",
  customer_phone: "9998887776",
  product: "Ashwagandha 60ct",
  order_amount: "649.00",
  payment_type: "prepaid",
  shipment_status: "in_transit",
}

function listData(rows: NDR[]) {
  return {
    success: true,
    message: "Success",
    data: rows,
    meta: { page: 1, page_size: 20, total_items: rows.length, total_pages: 1 },
  }
}

describe("NdrPage", () => {
  it("renders real NDR rows with enriched order/customer/product data", () => {
    setAuth(true)
    mockedUseNdrs.mockReturnValue(queryResult({ data: listData([NDR_ROW]) }))
    mockedUseUpdateNdr.mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useUpdateNdr>)
    mockedUseNdrReattempt.mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useNdrReattempt>)

    renderWithProviders(<NdrPage />)

    expect(screen.getByText("OMS-NDR-1")).toBeInTheDocument()
    expect(screen.getByText("Ananya Rao")).toBeInTheDocument()
    expect(screen.getByText("9998887776")).toBeInTheDocument()
    expect(screen.getByText("Ashwagandha 60ct")).toBeInTheDocument()
    expect(screen.getByText("Customer unavailable")).toBeInTheDocument()
  })

  it("shows loading skeletons while fetching", () => {
    setAuth(true)
    mockedUseNdrs.mockReturnValue(queryResult({ isLoading: true }))
    mockedUseUpdateNdr.mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useUpdateNdr>)
    mockedUseNdrReattempt.mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useNdrReattempt>)

    const { container } = renderWithProviders(<NdrPage />)
    expect(container.querySelectorAll("[data-slot='skeleton']").length).toBeGreaterThan(0)
  })

  it("shows an error state with retry", () => {
    setAuth(true)
    mockedUseNdrs.mockReturnValue(
      queryResult({ isError: true, error: new Error("Network down") })
    )
    mockedUseUpdateNdr.mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useUpdateNdr>)
    mockedUseNdrReattempt.mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useNdrReattempt>)

    renderWithProviders(<NdrPage />)
    expect(screen.getByText("Network down")).toBeInTheDocument()
  })

  it("shows an empty state when there are no NDR records", () => {
    setAuth(true)
    mockedUseNdrs.mockReturnValue(queryResult({ data: listData([]) }))
    mockedUseUpdateNdr.mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useUpdateNdr>)
    mockedUseNdrReattempt.mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useNdrReattempt>)

    renderWithProviders(<NdrPage />)
    expect(screen.getByText("No NDR records")).toBeInTheDocument()
  })

  it("navigates to the order detail page when a row is clicked", async () => {
    setAuth(true)
    mockedUseNdrs.mockReturnValue(queryResult({ data: listData([NDR_ROW]) }))
    mockedUseUpdateNdr.mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useUpdateNdr>)
    mockedUseNdrReattempt.mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useNdrReattempt>)

    renderWithProviders(<NdrPage />)
    await userEvent.click(screen.getByText("OMS-NDR-1"))
    expect(mockPush).toHaveBeenCalledWith("/orders/order-1")
  })

  it("reads status=open from the URL and requests it from the API without a full page reload", () => {
    mockSearchParams = new URLSearchParams("status=open")
    setAuth(true)
    mockedUseNdrs.mockReturnValue(queryResult({ data: listData([]) }))
    mockedUseUpdateNdr.mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useUpdateNdr>)
    mockedUseNdrReattempt.mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useNdrReattempt>)

    renderWithProviders(<NdrPage />)

    expect(mockedUseNdrs).toHaveBeenCalledWith(
      expect.objectContaining({ status: "open" })
    )
    mockSearchParams = new URLSearchParams()
  })

  it("computes Total/Open/Resolved KPI tiles from independent status-scoped queries", () => {
    setAuth(true)
    mockedUseNdrs.mockImplementation((params) => {
      if (params.status === "resolved") {
        return queryResult({
          data: {
            success: true,
            message: "Success",
            data: [],
            meta: { page: 1, page_size: 1, total_items: 3, total_pages: 3 },
          },
        })
      }
      return queryResult({
        data: {
          success: true,
          message: "Success",
          data: params.pageSize === 1 ? [] : [NDR_ROW],
          meta: { page: 1, page_size: params.pageSize, total_items: 10, total_pages: 10 },
        },
      })
    })
    mockedUseUpdateNdr.mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useUpdateNdr>)
    mockedUseNdrReattempt.mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useNdrReattempt>)

    renderWithProviders(<NdrPage />)

    expect(screen.getByText("Total NDR")).toBeInTheDocument()
    expect(screen.getAllByText("10").length).toBeGreaterThan(0)
    expect(screen.getByText("Resolved")).toBeInTheDocument()
    expect(screen.getAllByText("3").length).toBeGreaterThan(0)
    expect(screen.getByText("Open / Pending")).toBeInTheDocument()
    expect(screen.getAllByText("7").length).toBeGreaterThan(0)
  })
})

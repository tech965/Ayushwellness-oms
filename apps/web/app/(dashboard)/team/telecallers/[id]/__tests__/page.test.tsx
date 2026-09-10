import { describe, expect, it, vi } from "vitest"
import { screen } from "@testing-library/react"

import { renderWithProviders } from "@/test-utils/render-with-providers"
import TeamTelecallerWorkloadPage from "@/app/(dashboard)/team/telecallers/[id]/page"
import {
  useTelecallerDailyPerformance,
  useTelecallerOrders,
  useTelecallerSummary,
} from "@/services/team"

vi.mock("next/navigation", () => ({
  useParams: () => ({ id: "tc-1" }),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn() }),
  usePathname: () => "/team/telecallers/tc-1",
  useSearchParams: () => new URLSearchParams(),
}))

vi.mock("@/services/team", () => ({
  useTelecallerSummary: vi.fn(),
  useTelecallerDailyPerformance: vi.fn(),
  useTelecallerOrders: vi.fn(),
}))

const mockedUseTelecallerSummary = vi.mocked(useTelecallerSummary)
const mockedUseTelecallerDailyPerformance = vi.mocked(useTelecallerDailyPerformance)
const mockedUseTelecallerOrders = vi.mocked(useTelecallerOrders)

const SUMMARY = {
  telecaller_id: "tc-1",
  telecaller_name: "Sourabh",
  assigned: 40,
  called: 30,
  pending: 10,
  connected: 15,
  interested: 5,
  follow_ups: 4,
  confirmed: 8,
  not_interested: 9,
  cancelled: 2,
  fulfilled: 6,
  total_attempts: 52,
  average_call_attempts: 4.0,
  conversion_rate: 20.0,
  orders_confirmed: 12,
  shipped: 7,
  delivered: 5,
  ndr: 1,
  rto: 3,
}

const EMPTY_LIST = {
  data: { data: [], meta: { total_items: 0, page: 1, page_size: 20, total_pages: 0 } },
  isLoading: false,
  isError: false,
  error: null,
  refetch: vi.fn(),
}

describe("TeamTelecallerWorkloadPage (individual telecaller performance)", () => {
  it("renders real summary numbers in the page title and cards, and an empty daily chart", () => {
    mockedUseTelecallerSummary.mockReturnValue({
      isLoading: false,
      isError: false,
      error: null,
      data: SUMMARY,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useTelecallerSummary>)
    mockedUseTelecallerDailyPerformance.mockReturnValue({
      isLoading: false,
      isError: false,
      error: null,
      data: [],
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useTelecallerDailyPerformance>)
    mockedUseTelecallerOrders.mockReturnValue(
      EMPTY_LIST as unknown as ReturnType<typeof useTelecallerOrders>
    )

    renderWithProviders(<TeamTelecallerWorkloadPage />)

    expect(screen.getByText("Sourabh — Telecaller Performance")).toBeInTheDocument()
    expect(screen.getByText("40")).toBeInTheDocument() // Total Assigned
    expect(screen.getByText("4.0")).toBeInTheDocument() // Average Call Attempts
    expect(screen.getByText("6")).toBeInTheDocument() // Orders Fulfilled
    expect(screen.getByText("20%")).toBeInTheDocument() // Conversion Rate
    expect(screen.getByText("12")).toBeInTheDocument() // Orders Confirmed
    expect(screen.getByText("7")).toBeInTheDocument() // Shipped
    expect(screen.getByText("1")).toBeInTheDocument() // NDR
    expect(screen.getByText("3")).toBeInTheDocument() // RTO
    expect(screen.getByText("No calls logged in the selected range.")).toBeInTheDocument()
    expect(screen.getByText("No orders assigned")).toBeInTheDocument()
    expect(screen.getByRole("link", { name: /Back to Telecallers/i })).toBeInTheDocument()
  })

  it("falls back to a generic title and dash placeholders while the summary is loading", () => {
    mockedUseTelecallerSummary.mockReturnValue({
      isLoading: true,
      isError: false,
      error: null,
      data: undefined,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useTelecallerSummary>)
    mockedUseTelecallerDailyPerformance.mockReturnValue({
      isLoading: true,
      isError: false,
      error: null,
      data: undefined,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useTelecallerDailyPerformance>)
    mockedUseTelecallerOrders.mockReturnValue({
      ...EMPTY_LIST,
      isLoading: true,
      data: undefined,
    } as unknown as ReturnType<typeof useTelecallerOrders>)

    renderWithProviders(<TeamTelecallerWorkloadPage />)

    expect(screen.getByText("Telecaller Performance")).toBeInTheDocument()
    expect(screen.getAllByText("—").length).toBeGreaterThan(0)
  })
})

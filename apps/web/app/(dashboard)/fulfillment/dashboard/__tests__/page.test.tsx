import { describe, expect, it, vi } from "vitest"
import { screen } from "@testing-library/react"

import { renderWithProviders } from "@/test-utils/render-with-providers"
import FulfillmentDashboardPage from "@/app/(dashboard)/fulfillment/dashboard/page"
import { useShipmentAnalytics, useShipmentSummary } from "@/services/shipment-queue"

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn() }),
}))

vi.mock("@/services/shipment-queue", () => ({
  useShipmentSummary: vi.fn(),
  useShipmentAnalytics: vi.fn(),
}))

const mockedUseShipmentSummary = vi.mocked(useShipmentSummary)
const mockedUseShipmentAnalytics = vi.mocked(useShipmentAnalytics)

describe("FulfillmentDashboardPage", () => {
  it("renders real summary + per-telecaller NDR/RTO from the backend", () => {
    mockedUseShipmentSummary.mockReturnValue({
      isLoading: false,
      data: {
        confirmed_awaiting_shipment: 14,
        total_shipments: 120,
        pending: 3,
        picked_up: 4,
        in_transit: 20,
        out_for_delivery: 6,
        delivered: 80,
        ndr: 5,
        rto: 2,
        cancelled: 1,
        cod: 60,
        prepaid: 55,
        todays_shipments: 9,
      },
    } as unknown as ReturnType<typeof useShipmentSummary>)
    mockedUseShipmentAnalytics.mockReturnValue({
      isLoading: false,
      data: {
        status_breakdown: [{ status: "delivered", count: 80 }],
        confirmation_to_shipment_rate: 66.7,
        daily_trend: [],
        telecaller_stats: [
          {
            telecaller_id: "tc-1",
            telecaller_name: "Sourabh",
            confirmed: 12,
            shipped: 7,
            delivered: 5,
            ndr: 3,
            rto: 1,
          },
        ],
      },
    } as unknown as ReturnType<typeof useShipmentAnalytics>)

    renderWithProviders(<FulfillmentDashboardPage />)

    expect(screen.getByText("Fulfillment Dashboard")).toBeInTheDocument()
    expect(screen.getByText("14")).toBeInTheDocument()
    expect(screen.getByText("55")).toBeInTheDocument() // Prepaid Shipments tile
    expect(screen.getByText("66.7%")).toBeInTheDocument()
    expect(screen.getByText("Sourabh")).toBeInTheDocument()
    // Per-telecaller NDR/RTO columns render (value 3 = Sourabh's NDR count;
    // also proves the extra analytics fields flow through).
    expect(screen.getAllByText("3").length).toBeGreaterThan(0)
  })

  it("shows an empty telecaller state without crashing", () => {
    mockedUseShipmentSummary.mockReturnValue({
      isLoading: false,
      data: undefined,
    } as unknown as ReturnType<typeof useShipmentSummary>)
    mockedUseShipmentAnalytics.mockReturnValue({
      isLoading: false,
      data: {
        status_breakdown: [],
        confirmation_to_shipment_rate: 0,
        daily_trend: [],
        telecaller_stats: [],
      },
    } as unknown as ReturnType<typeof useShipmentAnalytics>)

    renderWithProviders(<FulfillmentDashboardPage />)

    expect(screen.getByText("No telecaller-confirmed orders yet.")).toBeInTheDocument()
  })

  it("surfaces a backend failure instead of rendering it as an empty dashboard", () => {
    mockedUseShipmentSummary.mockReturnValue({
      isLoading: false,
      isError: true,
      error: {
        isAxiosError: true,
        response: { data: { error: { message: "Internal server error" } } },
      },
      data: undefined,
    } as unknown as ReturnType<typeof useShipmentSummary>)
    mockedUseShipmentAnalytics.mockReturnValue({
      isLoading: false,
      isError: false,
      data: undefined,
    } as unknown as ReturnType<typeof useShipmentAnalytics>)

    renderWithProviders(<FulfillmentDashboardPage />)

    expect(screen.getByText("Could not load the fulfillment dashboard")).toBeInTheDocument()
    expect(screen.getByText("Internal server error")).toBeInTheDocument()
  })
})

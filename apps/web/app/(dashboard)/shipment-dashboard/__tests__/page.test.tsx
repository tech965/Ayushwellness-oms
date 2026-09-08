import { describe, expect, it, vi } from "vitest"
import { screen } from "@testing-library/react"

import { renderWithProviders } from "@/test-utils/render-with-providers"
import ShipmentDashboardPage from "@/app/(dashboard)/shipment-dashboard/page"
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

describe("ShipmentDashboardPage", () => {
  it("renders real summary numbers from the backend", () => {
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
        prepaid: 60,
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
          { telecaller_id: "tc-1", telecaller_name: "Sourabh", confirmed: 12, shipped: 7, delivered: 5 },
        ],
      },
    } as unknown as ReturnType<typeof useShipmentAnalytics>)

    renderWithProviders(<ShipmentDashboardPage />)

    expect(screen.getByText("14")).toBeInTheDocument() // Confirmed, Awaiting Shipment
    expect(screen.getByText("120")).toBeInTheDocument() // Total Shipments
    expect(screen.getAllByText("80").length).toBeGreaterThan(0) // Delivered (tile + breakdown)
    expect(screen.getByText("66.7%")).toBeInTheDocument() // Conversion rate
    expect(screen.getByText("Sourabh")).toBeInTheDocument()
  })

  it("does not crash with empty analytics/summary data", () => {
    mockedUseShipmentSummary.mockReturnValue({
      isLoading: false,
      data: undefined,
    } as unknown as ReturnType<typeof useShipmentSummary>)
    mockedUseShipmentAnalytics.mockReturnValue({
      isLoading: false,
      data: { status_breakdown: [], confirmation_to_shipment_rate: 0, daily_trend: [], telecaller_stats: [] },
    } as unknown as ReturnType<typeof useShipmentAnalytics>)

    renderWithProviders(<ShipmentDashboardPage />)

    expect(screen.getByText("Shipment Dashboard")).toBeInTheDocument()
    expect(screen.getByText("No telecaller-confirmed orders yet.")).toBeInTheDocument()
  })
})

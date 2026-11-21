import { beforeEach, describe, expect, it, vi } from "vitest"
import { screen } from "@testing-library/react"

import { renderWithProviders } from "@/test-utils/render-with-providers"
import ShipmentTrackingPage from "@/app/(dashboard)/shipment-tracking/page"
import { UNIFIED_SHIPMENT_STATUS_OPTIONS } from "@/types/shipment"
import {
  useShipmentStatusAnalytics,
  useShipmentStatusDrilldown,
  useShipmentStatusHistory,
} from "@/services/shipments"

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn() }),
  usePathname: () => "/shipment-tracking",
  useSearchParams: () => new URLSearchParams(),
}))
vi.mock("@/services/couriers", () => ({ useCouriers: () => ({ data: [] }) }))
vi.mock("@/services/shipments", () => ({
  useShipmentStatusAnalytics: vi.fn(),
  useShipmentStatusHistory: vi.fn(),
  useShipmentStatusDrilldown: vi.fn(),
  downloadShipmentStatusHistory: vi.fn(),
}))

const ALL = UNIFIED_SHIPMENT_STATUS_OPTIONS.map((o) => o.value)

function analytics(
  untracked: number,
  denominators: { eligible: number; shipped: number } = { eligible: 5, shipped: 2 }
) {
  return {
    total_orders: 10,
    total_eligible_orders: denominators.eligible,
    total_shipped_orders: denominators.shipped,
    status_breakdown: ALL.map((status) => ({
      status,
      count: status === "fulfilled_tracking_unavailable" ? untracked : 0,
      percent_of_total: 0,
    })),
    pickup_rate: 0,
    in_transit_rate: 0,
    out_for_delivery_rate: 0,
    delivery_rate: 50,
    rto_rate: 0,
  }
}

function mockQueries(
  untracked: number,
  denominators?: { eligible: number; shipped: number }
) {
  vi.mocked(useShipmentStatusAnalytics).mockReturnValue({
    data: analytics(untracked, denominators),
    isLoading: false,
    isError: false,
  } as unknown as ReturnType<typeof useShipmentStatusAnalytics>)
  vi.mocked(useShipmentStatusHistory).mockReturnValue({
    data: { daily: [] },
    isLoading: false,
    isError: false,
  } as unknown as ReturnType<typeof useShipmentStatusHistory>)
  vi.mocked(useShipmentStatusDrilldown).mockReturnValue({
    data: undefined,
    isLoading: false,
    isError: false,
  } as unknown as ReturnType<typeof useShipmentStatusDrilldown>)
}

describe("Shipment Tracking — Fulfilled – Tracking Unavailable status", () => {
  beforeEach(() => vi.clearAllMocks())

  it("is offered as a 13th status with the exact label", () => {
    expect(UNIFIED_SHIPMENT_STATUS_OPTIONS).toHaveLength(13)
    expect(
      UNIFIED_SHIPMENT_STATUS_OPTIONS.find(
        (o) => o.value === "fulfilled_tracking_unavailable"
      )?.label
    ).toBe("Fulfilled – Tracking Unavailable")
  })

  it("shows the status row and explains the exclusion when such orders exist", () => {
    mockQueries(7)
    renderWithProviders(<ShipmentTrackingPage />)
    expect(
      screen.getAllByText("Fulfilled – Tracking Unavailable").length
    ).toBeGreaterThan(0)
    expect(
      screen.getByText(/7 order\(s\) are marked fulfilled in Shopify/)
    ).toBeInTheDocument()
    expect(screen.getByText(/excluded from the Pickup, In-Transit/)).toBeInTheDocument()
  })

  it("shows no explanation note when there are none", () => {
    mockQueries(0)
    renderWithProviders(<ShipmentTrackingPage />)
    expect(screen.queryByText(/marked fulfilled in Shopify/)).not.toBeInTheDocument()
  })

  it("does not request a drilldown until a status is selected", () => {
    mockQueries(1)
    renderWithProviders(<ShipmentTrackingPage />)
    const args = vi.mocked(useShipmentStatusDrilldown).mock.calls[0][0]
    expect(args.enabled).toBe(false)
  })

  /** The big number on the StatTile card with this label (not its subtext). */
  function tileValue(label: string) {
    const card = screen.getByText(label).closest('[data-slot="card"]')
    return card?.querySelector("p.text-2xl")?.textContent ?? ""
  }

  describe("percentage KPIs", () => {
    it("shows — (not 0%) for every KPI whose denominator is zero", () => {
      mockQueries(3, { eligible: 0, shipped: 0 })
      renderWithProviders(<ShipmentTrackingPage />)
      for (const label of [
        "Pickup %",
        "In-Transit %",
        "Out for Delivery %",
        "Delivery %",
        "RTO %",
      ]) {
        const text = tileValue(label)
        expect(text).toBe("—")
      }
    })

    it("keeps a genuine 0% when the denominator is greater than zero", () => {
      // eligible=5, shipped=2, every rate is 0 except delivery (50).
      mockQueries(0, { eligible: 5, shipped: 2 })
      renderWithProviders(<ShipmentTrackingPage />)
      expect(tileValue("Pickup %")).toBe("0%")
      expect(tileValue("RTO %")).toBe("0%")
      expect(tileValue("Delivery %")).toBe("50%")
    })

    it("treats pickup and shipped denominators independently", () => {
      mockQueries(0, { eligible: 4, shipped: 0 })
      renderWithProviders(<ShipmentTrackingPage />)
      expect(tileValue("Pickup %")).toBe("0%")
      expect(tileValue("Delivery %")).toBe("—")
    })
  })
})

import { beforeEach, describe, expect, it, vi } from "vitest"
import { fireEvent, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { renderWithProviders } from "@/test-utils/render-with-providers"
import ShipmentTrackingPage from "@/app/(dashboard)/shipment-tracking/page"
import { STATUS_GROUP_META } from "@/lib/shipment-labels"
import {
  UNIFIED_SHIPMENT_STATUS_OPTIONS,
  type StatusGroupKey,
  type UnifiedShipmentStatus,
} from "@/types/shipment"
import {
  useShipmentStatusAnalytics,
  useShipmentStatusDrilldown,
  useShipmentStatusHistory,
} from "@/services/shipments"

const push = vi.fn()
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push, replace: vi.fn(), back: vi.fn() }),
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

const GROUPS: Record<StatusGroupKey, UnifiedShipmentStatus[]> = {
  new_orders: ["order_placed"],
  ready_for_shipping: ["ready_to_ship"],
  awaiting_pickup: ["pickup_pending"],
  picked_up: ["picked_up"],
  in_transit: ["in_transit", "rto_in_transit"],
  out_for_delivery: ["out_for_delivery"],
  delivered: ["delivered"],
  return_to_origin: ["rto_initiated", "rto_delivered"],
  delivery_issue: ["ndr"],
  cancelled: ["cancelled"],
  tracking_pending: ["fulfilled_tracking_unavailable"],
}

function analytics(counts: Partial<Record<StatusGroupKey, number>> = {}) {
  const groups = (Object.keys(GROUPS) as StatusGroupKey[]).map((key) => ({
    key,
    label: STATUS_GROUP_META[key].label,
    statuses: GROUPS[key],
    count: counts[key] ?? 0,
    percent_of_total: 0,
  }))
  const total = groups.reduce((sum, g) => sum + g.count, 0)
  return {
    total_orders: total,
    total_eligible_orders: total,
    total_shipped_orders: total,
    total_fulfilled: 40,
    total_delivered: 25,
    total_rto: 6,
    status_groups: groups,
    status_breakdown: UNIFIED_SHIPMENT_STATUS_OPTIONS.map((o) => ({
      status: o.value,
      count: 0,
      percent_of_total: 0,
    })),
    pickup_rate: 0,
    in_transit_rate: 0,
    out_for_delivery_rate: 0,
    delivery_rate: 0,
    rto_rate: 0,
  }
}

const ROW = {
  order_id: "o-1",
  order_number: "AW-1001",
  order_datetime: "2026-05-02T10:00:00Z",
  customer_name: "Asha",
  payment_type: "cod",
  sku_summary: "SKU-A",
  total_amount: "499.00",
  unified_status: "rto_in_transit",
  shipment_id: "s-1",
  awb: "AWB123",
  courier_name: "Bluedart Surface",
  expected_delivery_date: null,
  actual_delivery_date: null,
  shipment_status: "rto_initiated",
  current_location: "Mumbai Hub",
  last_tracking_update_at: "2026-05-04T08:00:00Z",
  pickup_date: "2026-05-02T14:00:00Z",
}

function mockQueries(
  counts: Partial<Record<StatusGroupKey, number>> = { new_orders: 5, delivered: 25 },
  drilldown: unknown = undefined
) {
  vi.mocked(useShipmentStatusAnalytics).mockReturnValue({
    data: analytics(counts),
    isLoading: false,
    isError: false,
  } as unknown as ReturnType<typeof useShipmentStatusAnalytics>)
  vi.mocked(useShipmentStatusHistory).mockReturnValue({
    data: { daily: [] },
    isLoading: false,
    isError: false,
  } as unknown as ReturnType<typeof useShipmentStatusHistory>)
  vi.mocked(useShipmentStatusDrilldown).mockReturnValue({
    data: drilldown,
    isLoading: false,
    isError: false,
    refetch: vi.fn(),
  } as unknown as ReturnType<typeof useShipmentStatusDrilldown>)
}

const lastDrilldownArgs = () => {
  const calls = vi.mocked(useShipmentStatusDrilldown).mock.calls
  return calls[calls.length - 1][0]
}

describe("Shipment Tracking — CEO view", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockQueries()
  })

  it("shows exactly four headline cards and none of the old percentage cards", () => {
    renderWithProviders(<ShipmentTrackingPage />)
    for (const label of [
      "Total Orders",
      "Total Fulfilled",
      "Total Delivered",
      "Total RTO",
    ]) {
      expect(screen.getByRole("button", { name: new RegExp(label) })).toBeInTheDocument()
    }
    for (const gone of [
      "Pickup %",
      "In-Transit %",
      "Out for Delivery %",
      "Delivery %",
      "RTO %",
    ]) {
      expect(screen.queryByText(gone)).not.toBeInTheDocument()
    }
    const cards = screen
      .getAllByRole("button", { pressed: false })
      .filter((el) => /^Total /.test(el.textContent ?? ""))
    expect(cards).toHaveLength(4)
  })

  it("shows the actual backend counts on the cards", () => {
    renderWithProviders(<ShipmentTrackingPage />)
    expect(screen.getByRole("button", { name: /Total Orders/ })).toHaveTextContent("30")
    expect(screen.getByRole("button", { name: /Total Fulfilled/ })).toHaveTextContent(
      "40"
    )
    expect(screen.getByRole("button", { name: /Total Delivered/ })).toHaveTextContent(
      "25"
    )
    expect(screen.getByRole("button", { name: /Total RTO/ })).toHaveTextContent("6")
  })

  it("does not request any orders until something is clicked", () => {
    renderWithProviders(<ShipmentTrackingPage />)
    expect(lastDrilldownArgs().enabled).toBe(false)
    expect(screen.queryByTestId("order-drilldown")).not.toBeInTheDocument()
  })

  it.each([
    ["Total Orders", "total"],
    ["Total Fulfilled", "fulfilled"],
    ["Total Delivered", "delivered"],
    ["Total RTO", "rto"],
  ])(
    "clicking %s lists that KPI's orders and marks the card selected",
    async (label, kpi) => {
      const user = userEvent.setup()
      renderWithProviders(<ShipmentTrackingPage />)
      await user.click(screen.getByRole("button", { name: new RegExp(label) }))
      const args = lastDrilldownArgs()
      expect(args).toMatchObject({ kpi, enabled: true, page: 1 })
      expect(args.group).toBeUndefined()
      expect(screen.getByRole("button", { name: new RegExp(label) })).toHaveAttribute(
        "aria-pressed",
        "true"
      )
      expect(screen.getByTestId("order-drilldown")).toBeInTheDocument()
    }
  )

  it("lists the 11 management categories, not the 13 technical statuses", () => {
    renderWithProviders(<ShipmentTrackingPage />)
    const table = screen.getByText("Status breakdown").closest("section") as HTMLElement
    const rows = within(table).getAllByRole("button")
    expect(rows).toHaveLength(11)
    for (const key of Object.keys(GROUPS) as StatusGroupKey[]) {
      expect(within(table).getByText(STATUS_GROUP_META[key].label)).toBeInTheDocument()
    }
    expect(within(table).queryByText("Pickup Pending")).not.toBeInTheDocument()
    expect(within(table).queryByText("RTO Initiated")).not.toBeInTheDocument()
  })

  it("clicking a category row lists exactly that category", async () => {
    const user = userEvent.setup()
    renderWithProviders(<ShipmentTrackingPage />)
    const table = screen.getByText("Status breakdown").closest("section") as HTMLElement
    await user.click(within(table).getByText("Return to Origin"))
    expect(lastDrilldownArgs()).toMatchObject({
      group: "return_to_origin",
      enabled: true,
    })
    expect(lastDrilldownArgs().kpi).toBeUndefined()
  })

  it("clicking a chart legend entry applies the same category filter", async () => {
    const user = userEvent.setup()
    renderWithProviders(<ShipmentTrackingPage />)
    const chart = screen
      .getByText("Orders by shipment status")
      .closest("section") as HTMLElement
    await user.click(
      within(chart).getByRole("button", { name: /Successfully Delivered/ })
    )
    expect(lastDrilldownArgs()).toMatchObject({ group: "delivered", enabled: true })
  })

  it("clicking the selected card again, or Clear selection, closes the list", async () => {
    const user = userEvent.setup()
    renderWithProviders(<ShipmentTrackingPage />)
    await user.click(screen.getByRole("button", { name: /Total Delivered/ }))
    expect(screen.getByTestId("order-drilldown")).toBeInTheDocument()
    await user.click(screen.getByRole("button", { name: /Clear selection/ }))
    expect(screen.queryByTestId("order-drilldown")).not.toBeInTheDocument()
    expect(lastDrilldownArgs().enabled).toBe(false)
  })

  it("keeps the original status, partner and tracking details in the order list", async () => {
    const user = userEvent.setup()
    mockQueries(
      { delivered: 25 },
      {
        data: [ROW],
        meta: { page: 1, page_size: 20, total_items: 1, total_pages: 1 },
      }
    )
    renderWithProviders(<ShipmentTrackingPage />)
    await user.click(screen.getByRole("button", { name: /Total RTO/ }))
    const list = screen.getByTestId("order-drilldown")
    expect(within(list).getByText("AW-1001")).toBeInTheDocument()
    expect(within(list).getByText("SKU-A")).toBeInTheDocument()
    expect(within(list).getByText("COD")).toBeInTheDocument()
    expect(within(list).getByText("Bluedart Surface")).toBeInTheDocument()
    // Friendly label up front, original technical status kept underneath.
    expect(within(list).getByText("In Transit")).toBeInTheDocument()
    expect(within(list).getByText(/Original status: RTO In Transit/)).toBeInTheDocument()
    expect(within(list).getByText("AWB123")).toBeInTheDocument()
    expect(within(list).getByText("Mumbai Hub")).toBeInTheDocument()
  })

  it("opens the existing order detail page when an order is clicked", async () => {
    const user = userEvent.setup()
    mockQueries(
      { delivered: 25 },
      { data: [ROW], meta: { page: 1, page_size: 20, total_items: 1, total_pages: 1 } }
    )
    renderWithProviders(<ShipmentTrackingPage />)
    await user.click(screen.getByRole("button", { name: /Total Orders/ }))
    await user.click(screen.getByText("AW-1001"))
    expect(push).toHaveBeenCalledWith("/orders/o-1")
  })

  it("searches server-side and returns to page 1", async () => {
    const user = userEvent.setup()
    mockQueries(
      { delivered: 25 },
      { data: [ROW], meta: { page: 1, page_size: 20, total_items: 40, total_pages: 2 } }
    )
    renderWithProviders(<ShipmentTrackingPage />)
    await user.click(screen.getByRole("button", { name: /Total Orders/ }))
    await user.click(screen.getByRole("button", { name: /Next/ }))
    expect(lastDrilldownArgs().page).toBe(2)
    await user.type(screen.getByLabelText("Search orders"), "AW-1001")
    await waitFor(() => expect(lastDrilldownArgs().q).toBe("AW-1001"))
    expect(lastDrilldownArgs().page).toBe(1)
  })

  it("passes the same filters to the cards and to the drilldown", async () => {
    const user = userEvent.setup()
    renderWithProviders(<ShipmentTrackingPage />)
    await user.click(screen.getByRole("button", { name: /Total Orders/ }))
    fireEvent.change(screen.getByPlaceholderText("Filter by SKU"), {
      target: { value: "ABC" },
    })
    const analyticsArgs = vi.mocked(useShipmentStatusAnalytics).mock.calls.at(-1)![0]
    const drill = lastDrilldownArgs()
    expect(analyticsArgs.sku).toBe("ABC")
    for (const key of [
      "sku",
      "payment_type",
      "courier_id",
      "date_from",
      "date_to",
    ] as const) {
      expect(drill[key]).toEqual(analyticsArgs[key])
    }
  })

  it("explains tracking-pending orders only when there are some", () => {
    mockQueries({ tracking_pending: 7 })
    const { unmount } = renderWithProviders(<ShipmentTrackingPage />)
    expect(
      screen.getByText(/7 order\(s\) are marked fulfilled in Shopify/)
    ).toBeInTheDocument()
    unmount()
    mockQueries({ tracking_pending: 0 })
    renderWithProviders(<ShipmentTrackingPage />)
    expect(screen.queryByText(/marked fulfilled in Shopify/)).not.toBeInTheDocument()
  })

  it("shows a loading state while the drilldown loads", async () => {
    const user = userEvent.setup()
    mockQueries()
    renderWithProviders(<ShipmentTrackingPage />)
    vi.mocked(useShipmentStatusDrilldown).mockReturnValue({
      data: undefined,
      isLoading: true,
      isError: false,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useShipmentStatusDrilldown>)
    await user.click(screen.getByRole("button", { name: /Total Orders/ }))
    expect(screen.getByTestId("order-drilldown")).toBeInTheDocument()
  })

  it("shows an empty state when a selection has no orders", async () => {
    const user = userEvent.setup()
    mockQueries(
      { delivered: 25 },
      { data: [], meta: { page: 1, page_size: 20, total_items: 0, total_pages: 0 } }
    )
    renderWithProviders(<ShipmentTrackingPage />)
    await user.click(screen.getByRole("button", { name: /Total RTO/ }))
    expect(screen.getByText("No orders in this selection")).toBeInTheDocument()
  })

  it("shows an error state with retry when the drilldown fails", async () => {
    const user = userEvent.setup()
    mockQueries()
    const refetch = vi.fn()
    renderWithProviders(<ShipmentTrackingPage />)
    vi.mocked(useShipmentStatusDrilldown).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      error: new Error("boom"),
      refetch,
    } as unknown as ReturnType<typeof useShipmentStatusDrilldown>)
    await user.click(screen.getByRole("button", { name: /Total Orders/ }))
    expect(screen.getByRole("button", { name: /retry|try again/i })).toBeInTheDocument()
  })
})

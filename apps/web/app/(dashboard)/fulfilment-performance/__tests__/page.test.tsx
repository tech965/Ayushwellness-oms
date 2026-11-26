import { beforeEach, describe, expect, it, vi } from "vitest"
import { screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { renderWithProviders } from "@/test-utils/render-with-providers"
import FulfilmentPerformancePage from "@/app/(dashboard)/fulfilment-performance/page"
import {
  useDeliveryPerformance,
  useDeliveryPerformanceHistory,
  useFulfilmentOrders,
  useFulfilmentPerformance,
  useFulfilmentPerformanceHistory,
} from "@/services/shipments"

const push = vi.fn()
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push, replace: vi.fn(), back: vi.fn() }),
  usePathname: () => "/fulfilment-performance",
  useSearchParams: () => new URLSearchParams(),
}))
vi.mock("@/services/couriers", () => ({ useCouriers: () => ({ data: [] }) }))
vi.mock("@/services/shipments", () => ({
  useFulfilmentPerformance: vi.fn(),
  useFulfilmentPerformanceHistory: vi.fn(),
  useDeliveryPerformance: vi.fn(),
  useDeliveryPerformanceHistory: vi.fn(),
  useFulfilmentOrders: vi.fn(),
}))
// recharts cannot size itself in jsdom; stand each bar in with a button so the
// page click -> filter wiring is what gets tested.
vi.mock("@/components/shared/clickable-charts", () => ({
  ClickableBarChart: ({
    ariaLabel,
    data,
    onSelect,
  }: {
    ariaLabel: string
    data: { key: string; label: string; value: number | null }[]
    onSelect: (key: string) => void
  }) => (
    <div aria-label={ariaLabel}>
      {data.map((d) => (
        <button key={d.key} onClick={() => onSelect(d.key)}>
          {`${ariaLabel} ${d.key} (${d.value})`}
        </button>
      ))}
    </div>
  ),
}))

const ok = <T,>(data: T) => ({ data, isLoading: false, isError: false, refetch: vi.fn() })

const PAGE_META = { page: 1, page_size: 20, total_items: 1, total_pages: 1 }

function setup(
  overrides: { fulfilment?: object; delivery?: object; orders?: unknown } = {}
) {
  vi.mocked(useFulfilmentPerformance).mockReturnValue(
    ok({
      avg_fulfilment_days: 1.5,
      fulfilled_order_count: 120,
      missing_pickup_timestamp_count: 1592,
      pending_fulfilment_count: 12,
      pending_over_24h_count: 5,
      pending_over_48h_count: 2,
      team_warehouse_breakdown: [],
      team_warehouse_attribution_available: false,
      team_warehouse_attribution_note:
        "a very long technical explanation that must not be shown",
      ...overrides.fulfilment,
    }) as never
  )
  vi.mocked(useDeliveryPerformance).mockReturnValue(
    ok({
      overall_avg_delivery_days: 4.25,
      delivered_order_count: 80,
      missing_timestamp_count: 3,
      by_courier: [
        {
          courier_key: "family:blue dart",
          courier_id: null,
          courier_name: "Blue Dart",
          service_names: ["Blue Dart Surface", "Bluedart Air"],
          avg_delivery_days: 3,
          delivered_order_count: 10,
        },
      ],
      by_zone: [],
      zone_is_state_based: true,
      unmapped_zone_order_count: 0,
      cod_avg_delivery_days: 5,
      cod_delivered_order_count: 30,
      prepaid_avg_delivery_days: 3.5,
      prepaid_delivered_order_count: 50,
      ...overrides.delivery,
    }) as never
  )
  vi.mocked(useFulfilmentPerformanceHistory).mockReturnValue(
    ok({
      daily: [
        { date: "2026-05-01", avg_fulfilment_days: 1.2, fulfilled_order_count: 7 },
        { date: "2026-05-02", avg_fulfilment_days: 2, fulfilled_order_count: 3 },
      ],
    }) as never
  )
  vi.mocked(useDeliveryPerformanceHistory).mockReturnValue(
    ok({
      daily: [{ date: "2026-05-03", avg_delivery_days: 4, delivered_order_count: 9 }],
    }) as never
  )
  vi.mocked(useFulfilmentOrders).mockReturnValue(
    ok(overrides.orders ?? undefined) as never
  )
}

const lastOrdersArgs = () => vi.mocked(useFulfilmentOrders).mock.calls.at(-1)![0]

describe("Fulfilment & Delivery Performance — CEO view", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    setup()
  })

  it("puts the four fulfilment/delivery KPIs at the top, in days, with order counts", () => {
    renderWithProviders(<FulfilmentPerformancePage />)
    const cards = {
      "Average Fulfilment Time": ["1.5 days", "Based on 120 orders"],
      "Average Delivery Time": ["4.3 days", "Based on 80 orders"],
      "COD Average Delivery Time": ["5.0 days", "Based on 30 orders"],
      "Prepaid Average Delivery Time": ["3.5 days", "Based on 50 orders"],
    }
    for (const [label, [value, basis]] of Object.entries(cards)) {
      const card = screen.getByRole("button", { name: new RegExp(`^${label}`) })
      expect(card).toHaveTextContent(value)
      expect(card).toHaveTextContent(basis)
    }
  })

  it("shows an unavailable average as a dash, never 0", () => {
    setup({ delivery: { cod_avg_delivery_days: null, cod_delivered_order_count: 0 } })
    renderWithProviders(<FulfilmentPerformancePage />)
    const card = screen.getByRole("button", { name: /^COD Average Delivery Time/ })
    expect(card).toHaveTextContent("—")
    expect(card).toHaveTextContent("Based on 0 orders")
  })

  it.each([
    ["Average Fulfilment Time", "fulfilled"],
    ["Average Delivery Time", "delivered"],
    ["COD Average Delivery Time", "cod_delivered"],
    ["Prepaid Average Delivery Time", "prepaid_delivered"],
  ])("clicking %s lists the %s orders", async (label, scope) => {
    const user = userEvent.setup()
    renderWithProviders(<FulfilmentPerformancePage />)
    expect(lastOrdersArgs().enabled).toBe(false)
    await user.click(screen.getByRole("button", { name: new RegExp(`^${label}`) }))
    expect(lastOrdersArgs()).toMatchObject({ scope, enabled: true, page: 1 })
    expect(lastOrdersArgs().day).toBeUndefined()
    expect(screen.getByRole("button", { name: new RegExp(`^${label}`) })).toHaveAttribute(
      "aria-pressed",
      "true"
    )
  })

  it("keeps pending work in its own clearly labelled section", () => {
    renderWithProviders(<FulfilmentPerformancePage />)
    const section = screen
      .getByRole("heading", { name: "Orders Awaiting Fulfilment" })
      .closest("section") as HTMLElement
    for (const label of [
      "Pending Fulfilment",
      "Pending for More Than 24 Hours",
      "Pending for More Than 48 Hours",
    ]) {
      expect(
        within(section).getByRole("button", { name: new RegExp(`^${label}`) })
      ).toBeInTheDocument()
    }
    expect(within(section).getByText(/not part of any average/)).toBeInTheDocument()
    expect(
      within(section).queryByRole("button", { name: /^Average Fulfilment Time/ })
    ).not.toBeInTheDocument()
  })

  it.each([
    ["Pending Fulfilment", "pending", "12"],
    ["Pending for More Than 24 Hours", "pending_24h", "5"],
    ["Pending for More Than 48 Hours", "pending_48h", "2"],
  ])("%s opens its exact orders", async (label, scope, count) => {
    const user = userEvent.setup()
    renderWithProviders(<FulfilmentPerformancePage />)
    const card = screen.getByRole("button", { name: new RegExp(`^${label}`) })
    expect(card).toHaveTextContent(count)
    await user.click(card)
    expect(lastOrdersArgs()).toMatchObject({ scope, enabled: true })
  })

  it("explains missing pickup dates in plain words with the backend count", () => {
    renderWithProviders(<FulfilmentPerformancePage />)
    expect(
      screen.getByText(
        "1,592 orders do not have a recorded courier pickup date. These orders are excluded from the average fulfilment calculation."
      )
    ).toBeInTheDocument()
    expect(
      screen.getByText(/3 delivered orders lack a pickup or delivery date/)
    ).toBeInTheDocument()
  })

  it("shows no missing-data notes when nothing is missing", () => {
    setup({
      fulfilment: { missing_pickup_timestamp_count: 0 },
      delivery: { missing_timestamp_count: 0 },
    })
    renderWithProviders(<FulfilmentPerformancePage />)
    expect(
      screen.queryByText(/do not have a recorded courier pickup date/)
    ).not.toBeInTheDocument()
    expect(screen.queryByText(/lack a pickup or delivery date/)).not.toBeInTheDocument()
  })

  it("keeps the team/warehouse explanation short and invents no figures", () => {
    renderWithProviders(<FulfilmentPerformancePage />)
    expect(screen.getByText(/by team or warehouse is not available/i)).toBeInTheDocument()
    expect(screen.queryByText(/very long technical explanation/)).not.toBeInTheDocument()
  })

  it("clicking a day on the fulfilment chart lists orders fulfilled that day", async () => {
    const user = userEvent.setup()
    renderWithProviders(<FulfilmentPerformancePage />)
    await user.click(
      screen.getByRole("button", { name: "Orders fulfilled per day 2026-05-02 (3)" })
    )
    expect(lastOrdersArgs()).toMatchObject({
      scope: "fulfilled",
      day: "2026-05-02",
      enabled: true,
    })
  })

  it("clicking a day on the delivery chart lists orders delivered that day", async () => {
    const user = userEvent.setup()
    renderWithProviders(<FulfilmentPerformancePage />)
    await user.click(
      screen.getByRole("button", { name: "Orders delivered per day 2026-05-03 (9)" })
    )
    expect(lastOrdersArgs()).toMatchObject({
      scope: "delivered",
      day: "2026-05-03",
      enabled: true,
    })
  })

  it("lists order number, payment, courier, pickup, delivery and duration", async () => {
    const user = userEvent.setup()
    setup({
      orders: {
        data: [
          {
            order_id: "o-9",
            order_number: "AW-9",
            order_datetime: "2026-05-01T00:00:00Z",
            payment_type: "prepaid",
            sku_summary: null,
            unified_status: "delivered",
            shipment_status: "delivered",
            awb: "AWB9",
            courier_name: "Delhivery",
            pickup_date: "2026-05-02T00:00:00Z",
            actual_delivery_date: "2026-05-05T00:00:00Z",
            fulfilment_days: 1,
            delivery_days: 3,
            pending_hours: null,
          },
        ],
        meta: PAGE_META,
      },
    })
    renderWithProviders(<FulfilmentPerformancePage />)
    await user.click(screen.getByRole("button", { name: /^Average Delivery Time/ }))
    const list = screen.getByTestId("order-drilldown")
    expect(within(list).getByText("AW-9")).toBeInTheDocument()
    expect(within(list).getByText("Prepaid")).toBeInTheDocument()
    expect(within(list).getByText("Delhivery")).toBeInTheDocument()
    expect(within(list).getByText("2 May 2026")).toBeInTheDocument()
    expect(within(list).getByText("5 May 2026")).toBeInTheDocument()
    expect(within(list).getByText("3.0 days")).toBeInTheDocument()
    await user.click(within(list).getByText("AW-9"))
    expect(push).toHaveBeenCalledWith("/orders/o-9")
  })

  it("shows how long a pending order has been waiting instead of a duration", async () => {
    const user = userEvent.setup()
    setup({
      orders: {
        data: [
          {
            order_id: "o-p",
            order_number: "AW-P",
            order_datetime: "2026-05-01T00:00:00Z",
            payment_type: "cod",
            sku_summary: null,
            unified_status: "ready_to_ship",
            shipment_status: null,
            awb: null,
            courier_name: null,
            pickup_date: null,
            actual_delivery_date: null,
            fulfilment_days: null,
            delivery_days: null,
            pending_hours: 50.4,
          },
        ],
        meta: PAGE_META,
      },
    })
    renderWithProviders(<FulfilmentPerformancePage />)
    await user.click(screen.getByRole("button", { name: /^Pending Fulfilment/ }))
    const list = screen.getByTestId("order-drilldown")
    expect(within(list).getByText("50 hours")).toBeInTheDocument()
    expect(within(list).getByText("Not picked up")).toBeInTheDocument()
  })

  it("passes the page filters to the aggregates and to the drilldown alike", async () => {
    const user = userEvent.setup()
    renderWithProviders(<FulfilmentPerformancePage />)
    await user.type(screen.getByPlaceholderText("Filter by SKU"), "AB")
    await user.click(screen.getByRole("button", { name: /^Average Delivery Time/ }))
    const agg = vi.mocked(useDeliveryPerformance).mock.calls.at(-1)![0]
    const drill = lastOrdersArgs()
    for (const key of [
      "sku",
      "payment_type",
      "courier_id",
      "date_from",
      "date_to",
    ] as const) {
      expect(drill[key]).toEqual(agg[key])
    }
    expect(agg.sku).toBe("AB")
  })

  it("clears the selection with Clear selection", async () => {
    const user = userEvent.setup()
    renderWithProviders(<FulfilmentPerformancePage />)
    await user.click(screen.getByRole("button", { name: /^Pending Fulfilment/ }))
    await user.click(screen.getByRole("button", { name: /Clear selection/ }))
    expect(screen.queryByTestId("order-drilldown")).not.toBeInTheDocument()
    expect(lastOrdersArgs().enabled).toBe(false)
  })

  it("keeps original service names under each carrier family", () => {
    renderWithProviders(<FulfilmentPerformancePage />)
    expect(screen.getByText("Blue Dart Surface · Bluedart Air")).toBeInTheDocument()
  })
})

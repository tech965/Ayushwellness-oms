import { beforeEach, describe, expect, it, vi } from "vitest"
import { screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { renderWithProviders } from "@/test-utils/render-with-providers"
import ShippingPartnersPage from "@/app/(dashboard)/shipping-partners/page"
import {
  usePartnerAllocation,
  usePartnerOrders,
  usePartnerPerformance,
} from "@/services/shipments"

const push = vi.fn()
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push, replace: vi.fn(), back: vi.fn() }),
  usePathname: () => "/shipping-partners",
  useSearchParams: () => new URLSearchParams(),
}))
vi.mock("@/services/shipments", () => ({
  usePartnerAllocation: vi.fn(),
  usePartnerPerformance: vi.fn(),
  usePartnerOrders: vi.fn(),
}))
// recharts cannot size itself in jsdom: each bar becomes a button so the
// page click -> filter wiring is what is tested.
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
          {`${ariaLabel} | ${d.label} | ${d.value}`}
        </button>
      ))}
    </div>
  ),
  ClickableGroupedBarChart: ({
    ariaLabel,
    data,
    series,
    onSelect,
  }: {
    ariaLabel: string
    data: { key: string; label: string; values: Record<string, number | null> }[]
    series: { key: string; label: string }[]
    onSelect: (key: string, series: string) => void
  }) => (
    <div aria-label={ariaLabel}>
      {data.flatMap((d) =>
        series.map((s) => (
          <button key={`${d.key}-${s.key}`} onClick={() => onSelect(d.key, s.key)}>
            {`${ariaLabel} | ${d.label} | ${s.label} | ${d.values[s.key]}`}
          </button>
        ))
      )}
    </div>
  ),
}))

const ok = <T,>(data: T) => ({ data, isLoading: false, isError: false, refetch: vi.fn() })

const BLUE = "family:blue dart"
const DELHIVERY = "family:delhivery"

function setup(orders?: unknown) {
  vi.mocked(usePartnerAllocation).mockReturnValue(
    ok({
      total_orders_allocated: 100,
      total_allocation_percent: 100,
      partners: [
        {
          courier_key: BLUE,
          courier_id: null,
          courier_name: "Blue Dart",
          service_names: ["Blue Dart Surface", "Bluedart Air"],
          orders_allocated: 60,
          allocation_percent: 60,
          avg_delivery_days: 3.5,
        },
        {
          courier_key: DELHIVERY,
          courier_id: null,
          courier_name: "Delhivery",
          service_names: ["Delhivery"],
          orders_allocated: 40,
          allocation_percent: 40,
          avg_delivery_days: null,
        },
      ],
    }) as never
  )
  vi.mocked(usePartnerPerformance).mockReturnValue(
    ok({
      shipping_cost_available: false,
      shipping_cost_unavailable_reason: "long internal reason",
      partners: [
        {
          courier_key: BLUE,
          courier_id: null,
          courier_name: "Blue Dart",
          service_names: ["Blue Dart Surface", "Bluedart Air"],
          total_allocated: 60,
          total_picked_up: 50,
          total_delivered: 40,
          delivery_success_rate: 80,
          rto_percentage: 10,
          avg_delivery_days: 3.5,
          avg_fulfilment_to_delivery_days: 4.5,
          missing_delivery_timestamp_count: 0,
          cod_delivery_success_rate: 70,
          prepaid_delivery_success_rate: 90,
          avg_shipping_cost_per_delivered_order: null,
          avg_shipping_cost_per_rto_order: null,
        },
        {
          courier_key: DELHIVERY,
          courier_id: null,
          courier_name: "Delhivery",
          service_names: ["Delhivery"],
          total_allocated: 40,
          total_picked_up: 0,
          total_delivered: 0,
          delivery_success_rate: null,
          rto_percentage: null,
          avg_delivery_days: null,
          avg_fulfilment_to_delivery_days: null,
          missing_delivery_timestamp_count: 0,
          cod_delivery_success_rate: null,
          prepaid_delivery_success_rate: null,
          avg_shipping_cost_per_delivered_order: null,
          avg_shipping_cost_per_rto_order: null,
        },
      ],
    }) as never
  )
  vi.mocked(usePartnerOrders).mockReturnValue(ok(orders) as never)
}

const lastOrdersArgs = () => vi.mocked(usePartnerOrders).mock.calls.at(-1)![0]

function section(heading: string): HTMLElement {
  return screen.getByRole("heading", { name: heading }).closest("section") as HTMLElement
}

describe("Shipping Partner Performance — CEO view", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    setup()
  })

  it("shows only the two allocation KPIs and no Total Allocation card", () => {
    renderWithProviders(<ShippingPartnersPage />)
    expect(
      screen.getByRole("button", { name: /Total Orders Allocated/ })
    ).toHaveTextContent("100")
    expect(
      screen.getByRole("button", { name: /Number of Shipping Partners/ })
    ).toHaveTextContent("2")
    expect(screen.queryByText("Total Allocation")).not.toBeInTheDocument()
    expect(screen.queryByText(/sums to 100%/)).not.toBeInTheDocument()
  })

  it("Total Orders Allocated lists every partner's orders", async () => {
    const user = userEvent.setup()
    renderWithProviders(<ShippingPartnersPage />)
    expect(lastOrdersArgs().enabled).toBe(false)
    await user.click(screen.getByRole("button", { name: /Total Orders Allocated/ }))
    expect(lastOrdersArgs()).toMatchObject({ metric: "allocated", enabled: true })
    expect(lastOrdersArgs().partner).toBeUndefined()
  })

  it("Number of Shipping Partners opens the list with a partner picker", async () => {
    const user = userEvent.setup()
    renderWithProviders(<ShippingPartnersPage />)
    await user.click(screen.getByRole("button", { name: /Number of Shipping Partners/ }))
    expect(lastOrdersArgs()).toMatchObject({ metric: "allocated", enabled: true })
    expect(screen.getByRole("combobox", { name: "Shipping partner" })).toBeInTheDocument()
    expect(screen.getByText("Orders by shipping partner")).toBeInTheDocument()
  })

  it("keeps the allocation columns and the original service names", () => {
    renderWithProviders(<ShippingPartnersPage />)
    const alloc = section("Order allocation")
    for (const header of [
      "Shipping partner",
      "Orders allocated",
      "Allocation %",
      "Avg. delivery time",
    ]) {
      expect(within(alloc).getByText(header)).toBeInTheDocument()
    }
    expect(
      within(alloc).getByText("Blue Dart Surface · Bluedart Air")
    ).toBeInTheDocument()
    expect(within(alloc).getByText("60.0%")).toBeInTheDocument()
  })

  it("clicking an allocation row lists that partner's orders", async () => {
    const user = userEvent.setup()
    renderWithProviders(<ShippingPartnersPage />)
    await user.click(within(section("Order allocation")).getByText("Delhivery"))
    expect(lastOrdersArgs()).toMatchObject({
      metric: "allocated",
      partner: DELHIVERY,
      enabled: true,
    })
  })

  it("keeps every comparison column and shows costs as Not available", () => {
    renderWithProviders(<ShippingPartnersPage />)
    const table = section("Partner performance comparison")
    for (const header of [
      "Allocated",
      "Picked up",
      "Delivered",
      "Delivery success %",
      "RTO %",
      "Avg. delivery days",
      "Avg. fulfilment-to-delivery days",
      "COD success %",
      "Prepaid success %",
    ]) {
      expect(within(table).getByText(header)).toBeInTheDocument()
    }
    expect(within(table).getAllByText("Not available").length).toBeGreaterThanOrEqual(2)
    expect(
      within(table).getByText(/Shipping costs are not available yet/)
    ).toBeInTheDocument()
    expect(within(table).queryByText(/long internal reason/)).not.toBeInTheDocument()
  })

  it("clicking a comparison row lists that partner's orders", async () => {
    const user = userEvent.setup()
    renderWithProviders(<ShippingPartnersPage />)
    await user.click(
      within(section("Partner performance comparison")).getByText("Blue Dart")
    )
    expect(lastOrdersArgs()).toMatchObject({ metric: "allocated", partner: BLUE })
  })

  it.each([
    ["Blue Dart picked-up orders", "picked_up"],
    ["Blue Dart delivered orders", "delivered"],
    ["Blue Dart RTO orders", "rto"],
    ["Blue Dart delivered COD orders", "cod_success"],
    ["Blue Dart delivered prepaid orders", "prepaid_success"],
  ])("clicking the %s figure filters to %s only", async (label, metric) => {
    const user = userEvent.setup()
    renderWithProviders(<ShippingPartnersPage />)
    await user.click(screen.getByRole("button", { name: label }))
    expect(lastOrdersArgs()).toMatchObject({ metric, partner: BLUE, enabled: true })
  })

  it("renders all four partner charts", () => {
    renderWithProviders(<ShippingPartnersPage />)
    for (const name of [
      "Orders allocated by shipping partner",
      "Delivery success rate by shipping partner",
      "RTO rate by shipping partner",
      "COD versus prepaid delivery success by shipping partner",
    ]) {
      expect(screen.getByLabelText(name)).toBeInTheDocument()
    }
  })

  it("keeps an unavailable rate as unavailable in the charts, not 0", () => {
    renderWithProviders(<ShippingPartnersPage />)
    expect(
      screen.getByRole("button", {
        name: "Delivery success rate by shipping partner | Delhivery | null",
      })
    ).toBeInTheDocument()
  })

  it.each([
    ["Orders allocated by shipping partner | Blue Dart | 60", "allocated"],
    ["Delivery success rate by shipping partner | Blue Dart | 80", "delivered"],
    ["RTO rate by shipping partner | Blue Dart | 10", "rto"],
  ])("clicking the bar %s applies the %s filter", async (bar, metric) => {
    const user = userEvent.setup()
    renderWithProviders(<ShippingPartnersPage />)
    await user.click(screen.getByRole("button", { name: bar }))
    expect(lastOrdersArgs()).toMatchObject({ metric, partner: BLUE, enabled: true })
  })

  it("clicking a COD or Prepaid bar filters to that payment mode's delivered orders", async () => {
    const user = userEvent.setup()
    renderWithProviders(<ShippingPartnersPage />)
    const chart = "COD versus prepaid delivery success by shipping partner"
    await user.click(
      screen.getByRole("button", { name: `${chart} | Blue Dart | COD success | 70` })
    )
    expect(lastOrdersArgs()).toMatchObject({ metric: "cod_success", partner: BLUE })
    await user.click(
      screen.getByRole("button", { name: `${chart} | Blue Dart | Prepaid success | 90` })
    )
    expect(lastOrdersArgs()).toMatchObject({ metric: "prepaid_success", partner: BLUE })
  })

  it("lists the order details the brief asks for, including the original service name", async () => {
    const user = userEvent.setup()
    setup({
      data: [
        {
          order_id: "o-3",
          order_number: "AW-3",
          order_datetime: "2026-05-01T00:00:00Z",
          payment_type: "cod",
          sku_summary: null,
          unified_status: "delivered",
          shipment_status: "delivered",
          awb: "AWB-3",
          courier_name: "Bluedart Surface - Select 500gm",
          pickup_date: "2026-05-02T00:00:00Z",
          actual_delivery_date: "2026-05-04T00:00:00Z",
          fulfilment_days: 1,
          delivery_days: 2,
          pending_hours: null,
        },
      ],
      meta: { page: 1, page_size: 20, total_items: 1, total_pages: 1 },
    })
    renderWithProviders(<ShippingPartnersPage />)
    await user.click(within(section("Order allocation")).getByText("Blue Dart"))
    const list = screen.getByTestId("order-drilldown")
    expect(within(list).getByText("AW-3")).toBeInTheDocument()
    expect(within(list).getByText("COD")).toBeInTheDocument()
    expect(within(list).getByText("Successfully Delivered")).toBeInTheDocument()
    expect(within(list).getByText(/Original status: Delivered/)).toBeInTheDocument()
    expect(within(list).getByText("AWB-3")).toBeInTheDocument()
    expect(within(list).getByText("Bluedart Surface - Select 500gm")).toBeInTheDocument()
    expect(within(list).getByText("2 May 2026")).toBeInTheDocument()
    expect(within(list).getByText("4 May 2026")).toBeInTheDocument()
    await user.click(within(list).getByText("AW-3"))
    expect(push).toHaveBeenCalledWith("/orders/o-3")
  })

  it("passes the page filters to the aggregates and to the drilldown alike", async () => {
    const user = userEvent.setup()
    renderWithProviders(<ShippingPartnersPage />)
    await user.type(screen.getByPlaceholderText("Filter by SKU"), "XY")
    await user.click(screen.getByRole("button", { name: /Total Orders Allocated/ }))
    const agg = vi.mocked(usePartnerPerformance).mock.calls.at(-1)![0]
    const drill = lastOrdersArgs()
    for (const key of ["sku", "payment_type", "date_from", "date_to"] as const) {
      expect(drill[key]).toEqual(agg[key])
    }
    expect(agg.sku).toBe("XY")
  })

  it("clears the selection", async () => {
    const user = userEvent.setup()
    renderWithProviders(<ShippingPartnersPage />)
    await user.click(screen.getByRole("button", { name: /Total Orders Allocated/ }))
    await user.click(screen.getByRole("button", { name: /Clear selection/ }))
    expect(screen.queryByTestId("order-drilldown")).not.toBeInTheDocument()
    expect(lastOrdersArgs().enabled).toBe(false)
  })

  describe("percentages", () => {
    it("shows each partner's share of allocated orders, success % and RTO % in the allocation table", () => {
      renderWithProviders(<ShippingPartnersPage />)
      const alloc = section("Order allocation")
      const blue = within(alloc).getByText("Blue Dart").closest("tr") as HTMLElement
      expect(blue).toHaveTextContent("60.0%")
      expect(blue).toHaveTextContent("80.0%")
      expect(blue).toHaveTextContent("10.0%")
      expect(blue).toHaveTextContent("of 50 picked up")
      const delhivery = within(alloc).getByText("Delhivery").closest("tr") as HTMLElement
      expect(delhivery).toHaveTextContent("40.0%")
    })

    it("shows a dash instead of 0% when a partner has no picked-up orders", () => {
      renderWithProviders(<ShippingPartnersPage />)
      const delhivery = within(section("Order allocation"))
        .getByText("Delhivery")
        .closest("tr") as HTMLElement
      expect(delhivery).toHaveTextContent("of 0 picked up")
      const cells = delhivery.querySelectorAll("td")
      // Delivery success % and RTO % columns: undefined, not 0%.
      expect(cells[3]).toHaveTextContent("—")
      expect(cells[4]).toHaveTextContent("—")
      expect(cells[3].textContent).not.toMatch(/^0/)
      expect(document.body.textContent).not.toMatch(/NaN|Infinity/)
    })

    it("adds a share-of-allocated column to the comparison table with its denominator", () => {
      renderWithProviders(<ShippingPartnersPage />)
      const table = section("Partner performance comparison")
      expect(within(table).getByText("Share of allocated")).toBeInTheDocument()
      const blue = within(table).getByText("Blue Dart").closest("tr") as HTMLElement
      expect(blue).toHaveTextContent("60.0%")
      expect(blue).toHaveTextContent("of 100 allocated")
      expect(blue).toHaveTextContent("80.0%")
      expect(blue).toHaveTextContent("of 50 picked up")
    })

    it("explains the denominators in the UI", () => {
      renderWithProviders(<ShippingPartnersPage />)
      expect(
        screen.getByText(/share of all allocated orders; delivery success/)
      ).toBeInTheDocument()
      expect(
        screen.getByText(/measured against the orders the courier has picked up/)
      ).toBeInTheDocument()
    })

    it("computes the share from the counts, not from fixed or pre-rounded values", () => {
      vi.mocked(usePartnerAllocation).mockReturnValue(
        ok({
          total_orders_allocated: 3,
          total_allocation_percent: 100,
          partners: [
            {
              courier_key: BLUE,
              courier_id: null,
              courier_name: "Blue Dart",
              service_names: [],
              orders_allocated: 1,
              allocation_percent: 99,
              avg_delivery_days: null,
            },
            {
              courier_key: DELHIVERY,
              courier_id: null,
              courier_name: "Delhivery",
              service_names: [],
              orders_allocated: 2,
              allocation_percent: 1,
              avg_delivery_days: null,
            },
          ],
        }) as never
      )
      renderWithProviders(<ShippingPartnersPage />)
      const alloc = section("Order allocation")
      expect(within(alloc).getByText("Blue Dart").closest("tr")).toHaveTextContent(
        "33.3%"
      )
      expect(within(alloc).getByText("Delhivery").closest("tr")).toHaveTextContent(
        "66.7%"
      )
    })

    it("formats backend rates to one decimal", () => {
      vi.mocked(usePartnerPerformance).mockReturnValue(
        ok({
          shipping_cost_available: false,
          shipping_cost_unavailable_reason: "x",
          partners: [
            {
              courier_key: BLUE,
              courier_id: null,
              courier_name: "Blue Dart",
              service_names: [],
              total_allocated: 11,
              total_picked_up: 11,
              total_delivered: 7,
              delivery_success_rate: 63.6364,
              rto_percentage: 22.7273,
              avg_delivery_days: 2.9166,
              avg_fulfilment_to_delivery_days: null,
              missing_delivery_timestamp_count: 0,
              cod_delivery_success_rate: 50,
              prepaid_delivery_success_rate: 33.3333,
              avg_shipping_cost_per_delivered_order: null,
              avg_shipping_cost_per_rto_order: null,
            },
          ],
        }) as never
      )
      renderWithProviders(<ShippingPartnersPage />)
      const row = within(section("Partner performance comparison"))
        .getByText("Blue Dart")
        .closest("tr") as HTMLElement
      expect(row).toHaveTextContent("63.6%")
      expect(row).toHaveTextContent("22.7%")
      expect(row).toHaveTextContent("50.0%")
      expect(row).toHaveTextContent("33.3%")
      expect(row.textContent).not.toMatch(/\d\.\d\d+%/)
    })

    it("shows the rates under the partner name for small screens, with the denominator", () => {
      renderWithProviders(<ShippingPartnersPage />)
      const alloc = section("Order allocation")
      const blue = within(alloc).getByText("Blue Dart").closest("tr") as HTMLElement
      const line = within(blue).getByTestId("partner-rates-mobile")
      expect(line).toHaveTextContent("Delivery success 80.0%")
      expect(line).toHaveTextContent("RTO 10.0%")
      expect(line).toHaveTextContent("of 50 picked up")
      expect(line.className).toContain("md:hidden")
      const delhivery = within(alloc).getByText("Delhivery").closest("tr") as HTMLElement
      expect(within(delhivery).getByTestId("partner-rates-mobile")).toHaveTextContent(
        "Delivery success — · RTO —"
      )
    })

    it("uses the same filters for the allocation shares and the performance rates", async () => {
      const user = userEvent.setup()
      renderWithProviders(<ShippingPartnersPage />)
      await user.type(screen.getByPlaceholderText("Filter by SKU"), "XY")
      const alloc = vi.mocked(usePartnerAllocation).mock.calls.at(-1)![0]
      const perf = vi.mocked(usePartnerPerformance).mock.calls.at(-1)![0]
      expect(alloc).toEqual(perf)
      expect(alloc.sku).toBe("XY")
    })

    it("keeps the rows and metric figures clickable with percentages shown", async () => {
      const user = userEvent.setup()
      renderWithProviders(<ShippingPartnersPage />)
      await user.click(
        screen.getByRole("button", { name: "Blue Dart delivered orders (success rate)" })
      )
      expect(lastOrdersArgs()).toMatchObject({ metric: "delivered", partner: BLUE })
    })
  })
})

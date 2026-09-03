import { describe, expect, it, vi } from "vitest"
import { screen } from "@testing-library/react"

import { renderWithProviders } from "@/test-utils/render-with-providers"
import OrdersPage from "@/app/(dashboard)/orders/page"
import { useOrders } from "@/services/orders"
import type { Order } from "@/types/order"

let mockSearchParams = new URLSearchParams()

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn() }),
  usePathname: () => "/orders",
  useSearchParams: () => mockSearchParams,
}))

vi.mock("@/services/orders", () => ({
  useOrders: vi.fn(),
  useExportOrders: () => ({ mutate: vi.fn(), isPending: false }),
}))

vi.mock("@/services/couriers", () => ({
  useCouriers: () => ({ data: [] }),
}))

const mockedUseOrders = vi.mocked(useOrders)

function baseQueryResult(overrides: Partial<ReturnType<typeof useOrders>>) {
  return {
    isLoading: false,
    isError: false,
    error: null,
    data: undefined,
    refetch: vi.fn(),
    ...overrides,
  } as unknown as ReturnType<typeof useOrders>
}

describe("OrdersPage", () => {
  it("shows skeletons while loading", () => {
    mockedUseOrders.mockReturnValue(baseQueryResult({ isLoading: true }))
    const { container } = renderWithProviders(<OrdersPage />)
    expect(container.querySelectorAll("[data-slot='skeleton']").length).toBeGreaterThan(0)
  })

  it("shows an error state with a retry action", () => {
    mockedUseOrders.mockReturnValue(
      baseQueryResult({ isError: true, error: new Error("Network down") })
    )
    renderWithProviders(<OrdersPage />)
    expect(screen.getByText("Network down")).toBeInTheDocument()
  })

  it("shows an empty state when there are no orders", () => {
    mockedUseOrders.mockReturnValue(
      baseQueryResult({
        data: {
          success: true,
          data: [],
          message: "Success",
          meta: { page: 1, page_size: 20, total_items: 0, total_pages: 0 },
        },
      })
    )
    renderWithProviders(<OrdersPage />)
    expect(screen.getByText("No orders found")).toBeInTheDocument()
  })

  it("renders a table row for each order", () => {
    mockedUseOrders.mockReturnValue(
      baseQueryResult({
        data: {
          success: true,
          message: "Success",
          data: [
            {
              id: "1",
              order_number: "OMS-1001",
              shopify_order_id: null,
              customer_id: null,
              order_datetime: "2026-01-01T00:00:00Z",
              currency: "INR",
              subtotal: "100.00",
              discount_amount: "0.00",
              tax_amount: "0.00",
              shipping_charge: "0.00",
              total_amount: "100.00",
              payment_type: "prepaid",
              payment_status: "pending",
              status: "pending",
              fulfillment_status: "unfulfilled",
              cancellation_status: "none",
              notes: null,
              shopify_tags: null,
              shopify_order_note: null,
              shipping_address: null,
              billing_address: null,
              source_system: "manual",
              created_at: "2026-01-01T00:00:00Z",
              updated_at: "2026-01-01T00:00:00Z",
            },
          ],
          meta: { page: 1, page_size: 20, total_items: 1, total_pages: 1 },
        },
      })
    )
    renderWithProviders(<OrdersPage />)
    expect(screen.getByText("OMS-1001")).toBeInTheDocument()
  })

  // Issue 2 regression: the visible-by-default "Shipment Status" column
  // must render Shopify's `fulfillment_status`, never Shiprocket's
  // `shipment_status` — Shiprocket's own status is still available, just
  // under a separate "Courier Status" column that stays off by default
  // (same as before), so the two sources are never conflated in the one
  // column most viewers actually see.
  it("renders the Shipment Status column from Shopify fulfillment_status, not Shiprocket shipment_status", () => {
    const order: Order = {
      id: "1",
      order_number: "OMS-SHIP-SRC",
      shopify_order_id: "1001",
      customer_id: null,
      order_datetime: "2026-01-01T00:00:00Z",
      currency: "INR",
      subtotal: "100.00",
      discount_amount: "0.00",
      tax_amount: "0.00",
      shipping_charge: "0.00",
      total_amount: "100.00",
      payment_type: "prepaid",
      payment_status: "paid",
      status: "confirmed",
      fulfillment_status: "unfulfilled",
      cancellation_status: "none",
      notes: null,
      shopify_tags: null,
      shopify_order_note: null,
      shipping_address: null,
      billing_address: null,
      source_system: "shopify",
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
      shipment_status: "pending",
    }

    mockedUseOrders.mockReturnValue(
      baseQueryResult({
        data: {
          success: true,
          message: "Success",
          data: [order],
          meta: { page: 1, page_size: 20, total_items: 1, total_pages: 1 },
        },
      })
    )
    renderWithProviders(<OrdersPage />)

    // Shopify's fulfillment_status ("unfulfilled") renders in the
    // default-visible Shipment Status column...
    expect(screen.getByText("Unfulfilled")).toBeInTheDocument()
    // ...while Shiprocket's shipment_status ("pending") is not shown,
    // because its column (Courier Status) is off by default.
    expect(screen.queryByText("Pending")).not.toBeInTheDocument()
  })

  // Regression coverage for the reported "Pending filter doesn't
  // correctly filter" bug: a dashboard drill-down link (e.g. from the
  // Order Status breakdown or the "Pending Orders" KPI) lands on
  // `/orders?status=pending` — this proves that URL state actually
  // reaches the `useOrders` API call as `status: "pending"`, not
  // silently dropped or mapped to the wrong field
  // (`OrderStatus.PENDING` vs. the unrelated `PaymentStatus.PENDING`,
  // which happens to share the same string).
  it("reads status=pending from the URL and requests it from the API", () => {
    mockSearchParams = new URLSearchParams("status=pending")
    mockedUseOrders.mockReturnValue(baseQueryResult({}))

    renderWithProviders(<OrdersPage />)

    expect(mockedUseOrders).toHaveBeenCalledWith(
      expect.objectContaining({ status: "pending" })
    )
    // And NOT sent as a payment_status filter, confirming the two
    // different "pending" enums aren't conflated on the way through.
    expect(mockedUseOrders).not.toHaveBeenCalledWith(
      expect.objectContaining({ payment_status: "pending" })
    )

    mockSearchParams = new URLSearchParams()
  })

  it("reads payment_type=cod from the URL and requests only COD orders", () => {
    mockSearchParams = new URLSearchParams("payment_type=cod")
    mockedUseOrders.mockReturnValue(baseQueryResult({}))

    renderWithProviders(<OrdersPage />)

    expect(mockedUseOrders).toHaveBeenCalledWith(
      expect.objectContaining({ payment_type: "cod" })
    )
    expect(mockedUseOrders).not.toHaveBeenCalledWith(
      expect.objectContaining({ payment_type: "prepaid" })
    )

    mockSearchParams = new URLSearchParams()
  })

  // Direct fix verification for the reported "Pending shows 0 orders"
  // confusion: three different fields (Order Status, Payment Status,
  // Shipment Status) can each be set to "Pending". Before this chip row
  // existed, a <Select> only ever displayed the bare label "Pending" —
  // now every active filter is labeled by field name, so it's always
  // unambiguous which one is actually applied.
  it("labels each active filter by field name so the three different 'Pending' filters are never ambiguous", () => {
    mockSearchParams = new URLSearchParams("shipment_status=pending")
    mockedUseOrders.mockReturnValue(baseQueryResult({}))
    renderWithProviders(<OrdersPage />)
    expect(screen.getByText("Shipment Status: Pending")).toBeInTheDocument()
    expect(screen.queryByText("Order Status: Pending")).not.toBeInTheDocument()
    mockSearchParams = new URLSearchParams()
  })

  it("shows Order Status: Pending distinctly when that field is the one set", () => {
    mockSearchParams = new URLSearchParams("status=pending")
    mockedUseOrders.mockReturnValue(baseQueryResult({}))
    renderWithProviders(<OrdersPage />)
    expect(screen.getByText("Order Status: Pending")).toBeInTheDocument()
    expect(screen.queryByText("Shipment Status: Pending")).not.toBeInTheDocument()
    expect(screen.queryByText("Payment Status: Pending")).not.toBeInTheDocument()
    mockSearchParams = new URLSearchParams()
  })

  // Regression test: `useOrders` keeps the previous filter's rows on
  // screen (`placeholderData`) while a newly filtered request is in
  // flight, so `isLoading` alone stays false — this proves the page
  // shows a visible "still updating" signal instead of silently
  // presenting the old filter's rows as if they already matched the new
  // (already-updated) filter chips, which is what the reported "filter
  // says one range, table shows another" symptom actually was.
  it("shows an updating indicator instead of silently presenting stale placeholder rows", () => {
    const ORDER_ROW: Order = {
      id: "1",
      order_number: "OMS-STALE-1",
      shopify_order_id: null,
      customer_id: null,
      order_datetime: "2026-01-01T00:00:00Z",
      currency: "INR",
      subtotal: "100.00",
      discount_amount: "0.00",
      tax_amount: "0.00",
      shipping_charge: "0.00",
      total_amount: "100.00",
      payment_type: "prepaid",
      payment_status: "pending",
      status: "pending",
      fulfillment_status: "unfulfilled",
      cancellation_status: "none",
      notes: null,
      shopify_tags: null,
      shopify_order_note: null,
      shipping_address: null,
      billing_address: null,
      source_system: "manual",
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
    }
    const data = {
      success: true,
      message: "Success",
      data: [ORDER_ROW],
      meta: { page: 1, page_size: 20, total_items: 1, total_pages: 1 },
    }

    mockedUseOrders.mockReturnValue(baseQueryResult({ data, isFetching: true }))
    renderWithProviders(<OrdersPage />)
    expect(screen.getByText("Updating results for the selected filters…")).toBeInTheDocument()
  })

  it("shows no updating indicator once the request for the current filters has settled", () => {
    mockedUseOrders.mockReturnValue(
      baseQueryResult({
        data: {
          success: true,
          message: "Success",
          data: [],
          meta: { page: 1, page_size: 20, total_items: 0, total_pages: 0 },
        },
        isFetching: false,
      })
    )
    renderWithProviders(<OrdersPage />)
    expect(
      screen.queryByText("Updating results for the selected filters…")
    ).not.toBeInTheDocument()
  })

  it("reads tag=VIP from the URL and requests it from the API", () => {
    mockSearchParams = new URLSearchParams("tag=VIP")
    mockedUseOrders.mockReturnValue(baseQueryResult({}))

    renderWithProviders(<OrdersPage />)

    expect(mockedUseOrders).toHaveBeenCalledWith(expect.objectContaining({ tag: "VIP" }))
    expect(screen.getByText("Tag: VIP")).toBeInTheDocument()

    mockSearchParams = new URLSearchParams()
  })

  it("combines multiple URL filters (payment_type + status) into one request", () => {
    mockSearchParams = new URLSearchParams("payment_type=cod&status=pending")
    mockedUseOrders.mockReturnValue(baseQueryResult({}))

    renderWithProviders(<OrdersPage />)

    expect(mockedUseOrders).toHaveBeenCalledWith(
      expect.objectContaining({ payment_type: "cod", status: "pending" })
    )

    mockSearchParams = new URLSearchParams()
  })
})

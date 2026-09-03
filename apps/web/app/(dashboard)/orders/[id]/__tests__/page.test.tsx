import { describe, expect, it, vi } from "vitest"
import { screen } from "@testing-library/react"

import { renderWithProviders } from "@/test-utils/render-with-providers"
import OrderDetailPage from "@/app/(dashboard)/orders/[id]/page"
import { useOrder, useOrderTimeline } from "@/services/orders"
import { usePaymentsForOrder } from "@/services/payments"
import { useShipmentsForOrder } from "@/services/shipments"
import { useReturnsForOrder } from "@/services/returns"
import { useRefundsForOrder } from "@/services/refunds"
import { useAuth } from "@/lib/auth-context"
import type { OrderDetail } from "@/types/order"

vi.mock("next/navigation", () => ({
  useParams: () => ({ id: "order-1" }),
  useSearchParams: () => new URLSearchParams(),
}))

vi.mock("@/services/orders", () => ({
  useOrder: vi.fn(),
  useOrderTimeline: vi.fn(),
  useShipOrderViaShiprocket: () => ({ mutate: vi.fn(), isPending: false }),
  useTransitionOrderStatus: () => ({ mutate: vi.fn(), isPending: false }),
}))

vi.mock("@/services/payments", () => ({
  usePaymentsForOrder: vi.fn(),
}))

vi.mock("@/services/shipments", () => ({
  useShipmentsForOrder: vi.fn(),
}))

vi.mock("@/services/returns", () => ({
  useReturnsForOrder: vi.fn(),
}))

vi.mock("@/services/refunds", () => ({
  useRefundsForOrder: vi.fn(),
}))

vi.mock("@/lib/auth-context", () => ({
  useAuth: vi.fn(),
}))

vi.mock("@/components/orders/cashfree-payment-card", () => ({
  CashfreePaymentCard: () => null,
}))

const mockedUseOrder = vi.mocked(useOrder)
const mockedUseOrderTimeline = vi.mocked(useOrderTimeline)
const mockedUsePaymentsForOrder = vi.mocked(usePaymentsForOrder)
const mockedUseShipmentsForOrder = vi.mocked(useShipmentsForOrder)
const mockedUseReturnsForOrder = vi.mocked(useReturnsForOrder)
const mockedUseRefundsForOrder = vi.mocked(useRefundsForOrder)
const mockedUseAuth = vi.mocked(useAuth)

function emptyListQuery<T>(data: T[] = []) {
  return {
    isLoading: false,
    isError: false,
    error: null,
    data,
    refetch: vi.fn(),
  } as unknown as T
}

const BASE_ORDER: OrderDetail = {
  id: "order-1",
  order_number: "AWL81350",
  shopify_order_id: "500",
  customer_id: null,
  order_datetime: "2026-08-01T00:00:00Z",
  currency: "INR",
  subtotal: "500.00",
  discount_amount: "0.00",
  tax_amount: "0.00",
  shipping_charge: "0.00",
  total_amount: "500.00",
  payment_type: "prepaid",
  payment_status: "paid",
  status: "confirmed",
  fulfillment_status: "unfulfilled",
  cancellation_status: "none",
  notes: null,
  shopify_tags: null,
  shopify_order_note: null,
  shopify_shipment_status: null,
  shipping_address: null,
  billing_address: null,
  source_system: "shopify",
  created_at: "2026-08-01T00:00:00Z",
  updated_at: "2026-08-01T00:00:00Z",
  items: [],
  customer: null,
}

function setUpQueries(order: OrderDetail) {
  mockedUseOrder.mockReturnValue({
    isLoading: false,
    isError: false,
    error: null,
    data: order,
    refetch: vi.fn(),
  } as unknown as ReturnType<typeof useOrder>)
  mockedUseOrderTimeline.mockReturnValue(
    emptyListQuery([]) as unknown as ReturnType<typeof useOrderTimeline>
  )
  mockedUsePaymentsForOrder.mockReturnValue(
    emptyListQuery([]) as unknown as ReturnType<typeof usePaymentsForOrder>
  )
  mockedUseShipmentsForOrder.mockReturnValue(
    emptyListQuery([]) as unknown as ReturnType<typeof useShipmentsForOrder>
  )
  mockedUseReturnsForOrder.mockReturnValue(
    emptyListQuery([]) as unknown as ReturnType<typeof useReturnsForOrder>
  )
  mockedUseRefundsForOrder.mockReturnValue(
    emptyListQuery([]) as unknown as ReturnType<typeof useRefundsForOrder>
  )
  mockedUseAuth.mockReturnValue({
    hasPermission: () => false,
  } as unknown as ReturnType<typeof useAuth>)
}

describe("OrderDetailPage — Shopify tags and order note", () => {
  it("renders Shopify tags as badges and the order note in quotes", () => {
    setUpQueries({
      ...BASE_ORDER,
      shopify_tags: ["Prepaid", "VIP"],
      shopify_order_note: "Please deliver after 6 PM",
    })

    renderWithProviders(<OrderDetailPage />)

    expect(screen.getByText("Shopify Tags & Order Note")).toBeInTheDocument()
    expect(screen.getByText("Prepaid")).toBeInTheDocument()
    expect(screen.getByText("VIP")).toBeInTheDocument()
    expect(screen.getByText("“Please deliver after 6 PM”")).toBeInTheDocument()
  })

  it("omits the section entirely when there are no tags and no note", () => {
    setUpQueries({ ...BASE_ORDER, shopify_tags: [], shopify_order_note: null })

    renderWithProviders(<OrderDetailPage />)

    expect(screen.queryByText("Shopify Tags & Order Note")).not.toBeInTheDocument()
  })

  it("omits the section for a manually created order with no Shopify data", () => {
    setUpQueries({ ...BASE_ORDER, shopify_tags: null, shopify_order_note: null })

    renderWithProviders(<OrderDetailPage />)

    expect(screen.queryByText("Shopify Tags & Order Note")).not.toBeInTheDocument()
  })

  it("renders the note without any tags when only a note is present", () => {
    setUpQueries({ ...BASE_ORDER, shopify_tags: [], shopify_order_note: "Leave at the gate" })

    renderWithProviders(<OrderDetailPage />)

    expect(screen.getByText("Shopify Tags & Order Note")).toBeInTheDocument()
    expect(screen.getByText("“Leave at the gate”")).toBeInTheDocument()
  })
})

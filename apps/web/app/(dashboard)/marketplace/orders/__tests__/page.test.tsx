import { describe, expect, it, vi } from "vitest"
import { screen } from "@testing-library/react"

import { renderWithProviders } from "@/test-utils/render-with-providers"
import MarketplaceOrdersPage from "@/app/(dashboard)/marketplace/orders/page"
import AmazonOrdersPage from "@/app/(dashboard)/marketplace/orders/amazon/page"
import { useMarketplaceOrders } from "@/services/marketplace"

const mockSearchParams = new URLSearchParams()

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn() }),
  usePathname: () => "/marketplace/orders",
  useSearchParams: () => mockSearchParams,
}))

vi.mock("@/services/marketplace", () => ({
  useMarketplaceOrders: vi.fn(),
  useMarketplaceDashboardSummary: vi.fn(),
}))

const mockedUseMarketplaceOrders = vi.mocked(useMarketplaceOrders)

function baseQueryResult(overrides: Partial<ReturnType<typeof useMarketplaceOrders>>) {
  return {
    isLoading: false,
    isError: false,
    isFetching: false,
    error: null,
    data: undefined,
    refetch: vi.fn(),
    ...overrides,
  } as unknown as ReturnType<typeof useMarketplaceOrders>
}

describe("MarketplaceOrdersPage", () => {
  it("shows an honest empty state when no marketplace orders exist yet (Phase 1, no live sync)", () => {
    mockedUseMarketplaceOrders.mockReturnValue(
      baseQueryResult({
        data: {
          success: true,
          message: "Success",
          data: [],
          meta: { page: 1, page_size: 20, total_items: 0, total_pages: 0 },
        },
      })
    )
    renderWithProviders(<MarketplaceOrdersPage />)
    expect(screen.getByText("No marketplace orders found")).toBeInTheDocument()
  })

  it("renders a row with the Marketplace/SKU/Tracking columns the requirement asks for", () => {
    mockedUseMarketplaceOrders.mockReturnValue(
      baseQueryResult({
        data: {
          success: true,
          message: "Success",
          data: [
            {
              id: "order-1",
              order_number: "MKT-0001",
              shopify_order_id: null,
              customer_id: "cust-1",
              order_datetime: "2026-11-05T00:00:00Z",
              currency: "INR",
              subtotal: "499.00",
              discount_amount: "0.00",
              tax_amount: "0.00",
              shipping_charge: "0.00",
              total_amount: "499.00",
              payment_type: "cod",
              payment_status: "pending",
              status: "pending",
              fulfillment_status: "unfulfilled",
              cancellation_status: "none",
              notes: null,
              shopify_tags: null,
              shopify_order_note: null,
              shopify_shipment_status: null,
              shipping_address: null,
              billing_address: null,
              source_system: "amazon",
              confirmed_by_telecaller_id: null,
              confirmed_at: null,
              created_at: "2026-11-05T00:00:00Z",
              updated_at: "2026-11-05T00:00:00Z",
              customer_name: "Priya Sharma",
              customer_phone: "9990000001",
              item_summary: "Ashwagandha Capsules",
              total_quantity: 1,
              shipment_status: null,
              tracking_number: null,
              external_id: "AMZ-ORDER-123",
              sku_summary: "ASHWA-ORANGE",
            },
          ],
          meta: { page: 1, page_size: 20, total_items: 1, total_pages: 1 },
        },
      })
    )
    renderWithProviders(<MarketplaceOrdersPage />)
    expect(screen.getByText("Amazon")).toBeInTheDocument()
    expect(screen.getByText("AMZ-ORDER-123")).toBeInTheDocument()
    expect(screen.getByText("ASHWA-ORANGE")).toBeInTheDocument()
    expect(screen.getByText("Priya Sharma")).toBeInTheDocument()
  })
})

describe("AmazonOrdersPage", () => {
  it("fixes the marketplace filter and shows the Amazon-specific empty state", () => {
    mockedUseMarketplaceOrders.mockReturnValue(
      baseQueryResult({
        data: {
          success: true,
          message: "Success",
          data: [],
          meta: { page: 1, page_size: 20, total_items: 0, total_pages: 0 },
        },
      })
    )
    renderWithProviders(<AmazonOrdersPage />)
    expect(screen.getByText("Amazon Orders")).toBeInTheDocument()
    expect(mockedUseMarketplaceOrders).toHaveBeenCalledWith(
      expect.objectContaining({ marketplace: "amazon" })
    )
  })
})

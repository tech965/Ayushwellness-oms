import { beforeEach, describe, expect, it, vi } from "vitest"
import { fireEvent, render, screen } from "@testing-library/react"

import { renderWithProviders } from "@/test-utils/render-with-providers"
import { LiveCatalog } from "@/components/inventory/live-catalog"
import { LiveRefundsCard } from "@/components/orders/live-refunds-card"
import { LivePaymentDetail } from "@/components/payments/live-payment-detail"
import VariantDetailPage from "@/app/(dashboard)/products/variants/[id]/page"
import { useAuth } from "@/lib/auth-context"
import { useOrder } from "@/services/orders"
import {
  useAutoMapBySku,
  useLiveOrderPayment,
  useLiveOrderRefunds,
  useLiveProducts,
  useLiveVariant,
} from "@/services/live"

vi.mock("next/link", () => ({
  default: ({ href, children }: { href: string; children: React.ReactNode }) => (
    <a href={href}>{children}</a>
  ),
}))
vi.mock("next/navigation", () => ({ useParams: () => ({ id: "201" }) }))
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn() } }))
vi.mock("@/lib/auth-context", () => ({ useAuth: vi.fn() }))
vi.mock("@/components/orders/cashfree-payment-card", () => ({ loadCashfreeSdk: vi.fn() }))
vi.mock("@/services/orders", () => ({ useOrder: vi.fn() }))
vi.mock("@/services/live", () => ({
  useLiveProducts: vi.fn(),
  useAutoMapBySku: vi.fn(),
  useLiveOrderRefunds: vi.fn(),
  useLiveOrderPayment: vi.fn(),
  useLiveCreateCheckout: vi.fn(() => ({ mutateAsync: vi.fn(), isPending: false })),
  useLiveVariant: vi.fn(),
}))

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(useAuth).mockReturnValue({ hasPermission: () => false } as never)
  vi.mocked(useAutoMapBySku).mockReturnValue({ mutateAsync: vi.fn(), isPending: false } as never)
})

describe("LiveRefundsCard", () => {
  it("shows live refunds and the cancellation state Shopify reports", () => {
    vi.mocked(useLiveOrderRefunds).mockReturnValue({
      data: {
        cancelled_at: "2026-10-05T00:00:00Z",
        cancel_reason: "CUSTOMER",
        total_refunded: "100.00",
        currency: "INR",
        refunds: [
          { id: "r1", created_at: null, processed_at: "2026-10-04T10:00:00Z", note: "damaged", amount: "100.00" },
        ],
      },
      isLoading: false,
      isError: false,
    } as never)

    renderWithProviders(<LiveRefundsCard orderId="o1" />)

    expect(screen.getByTestId("live-cancelled")).toHaveTextContent(/Cancelled in Shopify/)
    expect(screen.getByText(/damaged/)).toBeInTheDocument()
  })

  it("reports a Shopify failure instead of saying there are no refunds", () => {
    vi.mocked(useLiveOrderRefunds).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      error: new Error("Shopify rate limit reached."),
    } as never)

    renderWithProviders(<LiveRefundsCard orderId="o1" />)

    expect(screen.getByText(/Could not load refunds from Shopify/)).toBeInTheDocument()
    expect(screen.queryByText("No refunds.")).toBeNull()
  })
})

describe("LivePaymentDetail", () => {
  it("is keyed by order id and shows the live payment panel", () => {
    vi.mocked(useOrder).mockReturnValue({
      data: { order_number: "#1001", payment_status: "paid" },
      isLoading: false,
      isError: false,
    } as never)
    vi.mocked(useLiveOrderPayment).mockReturnValue({
      data: {
        found: true, paid: true, outcome: "match", shopify_payment_status: "paid",
        cashfree_order_id: "1001", payments: [],
      },
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    } as never)

    renderWithProviders(<LivePaymentDetail orderId="o1" />)

    expect(screen.getByText("Payment for #1001")).toBeInTheDocument()
    expect(screen.getByTestId("live-payment-card")).toBeInTheDocument()
    expect(screen.getByText("Open order #1001").closest("a")).toHaveAttribute("href", "/orders/o1")
  })
})

describe("VariantDetailPage", () => {
  it("labels Shopify fields and OMS stock separately and never treats Shopify's count as stock", () => {
    vi.mocked(useLiveVariant).mockReturnValue({
      data: {
        external_variant_id: "201", external_product_id: "20", product_title: "Herbal Masala",
        sku: "MASALA-50", title: "50g", price: "249.00", compare_at_price: null, barcode: null,
        image_url: null, mapped: true, shopify_inventory_quantity: 999,
        oms: {
          variant_id: "v1", oms_sku: "MASALA-50", physical_quantity: 12, reserved_quantity: 2,
          available_to_promise: 10, pack_size: 1, packets_per_box: 1,
        },
      },
      isLoading: false,
      isError: false,
    } as never)

    renderWithProviders(<VariantDetailPage />)

    expect(screen.getByTestId("variant-detail")).toBeInTheDocument()
    expect(screen.getByText(/Physical: 12 boxes/)).toBeInTheDocument()
    expect(screen.getByText(/Available to promise: 10 boxes/)).toBeInTheDocument()
    expect(screen.getByText(/information only/)).toHaveTextContent("999")
  })

  it("explains an unmapped or unknown variant plainly", () => {
    vi.mocked(useLiveVariant).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      error: new Error("Variant not found. Only variants already mapped can be opened."),
    } as never)

    renderWithProviders(<VariantDetailPage />)

    expect(screen.getByText(/Could not open this variant/)).toBeInTheDocument()
    expect(screen.getByText(/already mapped/)).toBeInTheDocument()
  })
})

describe("LiveCatalog filters", () => {
  it("passes search, SKU, status and mapping filters to the live query and resets paging", () => {
    vi.mocked(useLiveProducts).mockReturnValue({
      data: { items: [], next_cursor: "c2", has_more: true, page_filtered: false },
      isLoading: false,
      isError: false,
    } as never)

    render(<LiveCatalog />)
    fireEvent.change(screen.getByLabelText("Search title"), { target: { value: "masala" } })
    fireEvent.change(screen.getByLabelText("SKU"), { target: { value: "M-50" } })
    fireEvent.change(screen.getByLabelText("Status"), { target: { value: "active" } })
    fireEvent.change(screen.getByLabelText("Mapping"), { target: { value: "false" } })

    const last = vi.mocked(useLiveProducts).mock.calls.at(-1)?.[0]
    expect(last).toMatchObject({ q: "masala", sku: "M-50", status: "active", mapped: false })
    expect(last?.cursor).toBeUndefined()
  })

  it("links only mapped variants to their detail page", () => {
    vi.mocked(useLiveProducts).mockReturnValue({
      data: {
        next_cursor: null, has_more: false, page_filtered: false,
        items: [
          {
            external_product_id: "20", title: "Herbal Masala", description: null, vendor: null,
            product_type: null, status: "active", image_url: null,
            variants: [
              { external_variant_id: "201", sku: "A", title: "Mapped one", price: "1", compare_at_price: null, barcode: null, image_url: null, mapped: true, shopify_inventory_quantity: 1, oms: null },
              { external_variant_id: "202", sku: "B", title: "Loose one", price: "1", compare_at_price: null, barcode: null, image_url: null, mapped: false, shopify_inventory_quantity: 1, oms: null },
            ],
          },
        ],
      },
      isLoading: false,
      isError: false,
    } as never)

    render(<LiveCatalog />)

    expect(screen.getByText("Mapped one").closest("a")).toHaveAttribute("href", "/products/variants/201")
    expect(screen.getByText("Loose one").closest("a")).toBeNull()
  })
})

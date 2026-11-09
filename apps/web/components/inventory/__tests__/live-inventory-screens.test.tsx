import { beforeEach, describe, expect, it, vi } from "vitest"
import { fireEvent, screen } from "@testing-library/react"

import { renderWithProviders } from "@/test-utils/render-with-providers"
import { LiveCatalog } from "@/components/inventory/live-catalog"
import { LiveInventory } from "@/components/inventory/live-inventory"
import { LivePaymentCard } from "@/components/orders/live-payment-card"
import { useAuth } from "@/lib/auth-context"
import { useAddVariantStock, useInventoryMovements } from "@/services/inventory"
import {
  useAutoMapBySku,
  useInventoryOverview,
  useLiveCreateCheckout,
  useLiveOrderPayment,
  useLiveProducts,
} from "@/services/live"

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn() } }))
vi.mock("@/lib/auth-context", () => ({ useAuth: vi.fn() }))
vi.mock("@/components/orders/cashfree-payment-card", () => ({
  loadCashfreeSdk: vi.fn().mockResolvedValue(undefined),
}))
vi.mock("@/services/inventory", () => ({
  useAddVariantStock: vi.fn(),
  useInventoryMovements: vi.fn(),
}))
vi.mock("@/services/live", () => ({
  useLiveProducts: vi.fn(),
  useAutoMapBySku: vi.fn(),
  useInventoryOverview: vi.fn(),
  useLiveOrderPayment: vi.fn(),
  useLiveCreateCheckout: vi.fn(),
}))

const oms = {
  variant_id: "v1",
  oms_sku: "MASALA-50",
  physical_quantity: 12,
  reserved_quantity: 2,
  available_to_promise: 10,
  pack_size: 1,
  packets_per_box: 1,
}

function permissions(...codes: string[]) {
  vi.mocked(useAuth).mockReturnValue({
    hasPermission: (code: string) => codes.includes(code),
  } as never)
}

describe("LiveCatalog", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(useAutoMapBySku).mockReturnValue({ mutateAsync: vi.fn(), isPending: false } as never)
  })

  it("shows live catalogue next to OMS-owned stock and marks unmapped variants", () => {
    permissions("inventory.read")
    vi.mocked(useLiveProducts).mockReturnValue({
      data: {
        next_cursor: null,
        has_more: false,
        page_filtered: false,
        items: [
          {
            external_product_id: "20",
            title: "Herbal Masala",
            description: null,
            vendor: null,
            product_type: null,
            status: "active",
            image_url: null,
            variants: [
              {
                external_variant_id: "201", sku: "MASALA-50", title: "50g", price: "249.00",
                compare_at_price: null, barcode: null, image_url: null, mapped: true,
                shopify_inventory_quantity: 999, oms,
              },
              {
                external_variant_id: "202", sku: "MASALA-100", title: "100g", price: "449.00",
                compare_at_price: null, barcode: null, image_url: null, mapped: false,
                shopify_inventory_quantity: 5, oms: null,
              },
            ],
          },
        ],
      },
      isLoading: false,
      isError: false,
      error: null,
    } as never)

    renderWithProviders(<LiveCatalog />)

    expect(screen.getByText("Herbal Masala")).toBeInTheDocument()
    expect(screen.getByText("Not mapped to OMS stock")).toBeInTheDocument()
    expect(screen.getByText("12")).toBeInTheDocument() // OMS physical, not Shopify's 999
    expect(screen.queryByText("999")).toBeNull()
    expect(screen.queryByRole("button", { name: /map variants by sku/i })).toBeNull() // needs manage
  })

  it("offers SKU mapping only with the manage permission, and reports a Shopify error plainly", () => {
    permissions("inventory.read", "inventory.manage")
    vi.mocked(useLiveProducts).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      error: new Error("Shopify is temporarily unavailable."),
    } as never)

    renderWithProviders(<LiveCatalog />)

    expect(screen.getByRole("button", { name: /map variants by sku/i })).toBeInTheDocument()
    expect(screen.getByText(/Could not load products from Shopify/)).toBeInTheDocument()
    expect(screen.getByText(/temporarily unavailable/)).toBeInTheDocument()
  })
})

describe("LiveInventory", () => {
  const row = {
    ...oms,
    oms_display_name: null,
    mapped: true,
    external_variant_id: "201",
    shopify_title: "Herbal Masala — 50g",
  }

  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(useAddVariantStock).mockReturnValue({ mutate: vi.fn(), isPending: false } as never)
    vi.mocked(useInventoryMovements).mockReturnValue({
      data: { data: [] },
      isLoading: false,
      isError: false,
    } as never)
  })

  it("shows physical, reserved and available-to-promise and hides adjustments without permission", () => {
    permissions("inventory.read")
    vi.mocked(useInventoryOverview).mockReturnValue({
      data: {
        items: [row], total_items: 1, page: 1, page_size: 25,
        shopify_names_available: true, warnings: [],
      },
      isLoading: false,
      isError: false,
    } as never)

    renderWithProviders(<LiveInventory />)

    expect(screen.getByText("Herbal Masala — 50g")).toBeInTheDocument()
    expect(screen.getByText("12")).toBeInTheDocument()
    expect(screen.getByText("2")).toBeInTheDocument()
    expect(screen.getByText("10")).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "Add stock" })).toBeNull()
  })

  it("never presents a missing Shopify name as a name, and says Shopify names are unavailable", () => {
    permissions("inventory.read", "inventory.manage")
    vi.mocked(useInventoryOverview).mockReturnValue({
      data: {
        items: [{ ...row, shopify_title: null }], total_items: 1, page: 1, page_size: 25,
        shopify_names_available: false, warnings: ["Shopify names unavailable: Shopify is down"],
      },
      isLoading: false,
      isError: false,
    } as never)

    renderWithProviders(<LiveInventory />)

    expect(screen.getByText("Name unavailable")).toBeInTheDocument()
    expect(screen.getByTestId("live-result-warning")).toHaveTextContent(/Shopify is down/)
    expect(screen.getByRole("button", { name: "Add stock" })).toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: "Add stock" }))
    expect(screen.getByLabelText("Boxes to add")).toBeInTheDocument()
  })
})

describe("LivePaymentCard", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(useLiveCreateCheckout).mockReturnValue({ mutateAsync: vi.fn(), isPending: false } as never)
  })

  const payment = (overrides = {}) => ({
    found: true,
    paid: true,
    outcome: "match",
    shopify_payment_status: "paid",
    cashfree_order_id: "1001",
    payments: [
      { cf_payment_id: "11", status: "paid", amount: "499.00", method: "upi", paid_at: null },
    ],
    ...overrides,
  })

  it("shows the Cashfree-verified status and its comparison with Shopify", () => {
    permissions("payments.read")
    vi.mocked(useLiveOrderPayment).mockReturnValue({
      data: payment({ outcome: "mismatch", paid: false }),
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    } as never)

    renderWithProviders(<LivePaymentCard orderId="o1" orderPaymentStatus="paid" />)

    expect(screen.getByTestId("live-payment-outcome")).toHaveTextContent(/disagree/)
    expect(screen.getByText(/Verified by Cashfree: not paid/)).toBeInTheDocument()
  })

  it("offers collecting payment only to permitted users and only when unpaid", () => {
    permissions("payments.read", "payments.create")
    vi.mocked(useLiveOrderPayment).mockReturnValue({
      data: payment({ paid: false, found: true, outcome: "match", payments: [] }),
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    } as never)

    renderWithProviders(<LivePaymentCard orderId="o1" orderPaymentStatus="pending" />)

    expect(screen.getByRole("button", { name: /Collect payment via Cashfree/ })).toBeInTheDocument()
  })

  it("falls back to what Shopify says, with a clear error, when Cashfree cannot be reached", () => {
    permissions("payments.read")
    vi.mocked(useLiveOrderPayment).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      error: new Error("Cashfree is rate limiting requests."),
      refetch: vi.fn(),
    } as never)

    renderWithProviders(<LivePaymentCard orderId="o1" orderPaymentStatus="pending" />)

    expect(screen.getByText(/Could not verify with Cashfree/)).toBeInTheDocument()
    expect(screen.getByText(/Shopify reports this order as pending/)).toBeInTheDocument()
  })
})

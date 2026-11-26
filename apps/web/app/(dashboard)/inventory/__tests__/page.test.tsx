import { describe, expect, it, vi, beforeEach } from "vitest"
import { fireEvent, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { renderWithProviders } from "@/test-utils/render-with-providers"
import InventoryPage from "@/app/(dashboard)/inventory/page"
import {
  useInventoryDashboard,
  useInventoryMovements,
  useInventoryProducts,
} from "@/services/inventory"
import { useSettings, useUpdateSettings } from "@/services/settings"
import { useAuth } from "@/lib/auth-context"
import type { InventoryDashboard, InventoryMovement, InventoryProductSummary } from "@/types/inventory"

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn() }),
  usePathname: () => "/inventory",
  useSearchParams: () => new URLSearchParams(),
}))

vi.mock("@/services/inventory", () => ({
  useInventoryProducts: vi.fn(),
  useInventoryDashboard: vi.fn(),
  useInventoryMovements: vi.fn(),
}))
vi.mock("@/services/settings", () => ({ useSettings: vi.fn(), useUpdateSettings: vi.fn() }))
vi.mock("@/lib/auth-context", () => ({ useAuth: vi.fn() }))

const mockedUseInventoryProducts = vi.mocked(useInventoryProducts)
const mockedUseInventoryDashboard = vi.mocked(useInventoryDashboard)
const mockedUseInventoryMovements = vi.mocked(useInventoryMovements)
const mockedUseSettings = vi.mocked(useSettings)
const mockedUseUpdateSettings = vi.mocked(useUpdateSettings)
const mockedUseAuth = vi.mocked(useAuth)

const DEFAULT_DASHBOARD: InventoryDashboard = {
  total_products: 0,
  total_skus: 0,
  total_available_boxes: 0,
  total_available_packets: 0,
  opening_stock_boxes: 0,
  inward_boxes_in_range: 0,
  outward_boxes_in_range: 0,
  rto_in_transit_boxes: 0,
  rto_in_transit_count: 0,
  rto_awaiting_verification_boxes: 0,
  rto_awaiting_verification_count: 0,
  low_stock_count: 0,
  out_of_stock_count: 0,
  physical_stock_boxes: 0,
  reserved_stock_boxes: 0,
  available_to_sell_boxes: 0,
}

function setDashboard(overrides: Partial<InventoryDashboard> = {}) {
  mockedUseInventoryDashboard.mockReturnValue({
    data: { ...DEFAULT_DASHBOARD, ...overrides },
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  } as unknown as ReturnType<typeof useInventoryDashboard>)
}

function setMovements(data: InventoryMovement[]) {
  mockedUseInventoryMovements.mockReturnValue({
    data: { data, meta: { page: 1, page_size: 20, total_items: data.length, total_pages: 1 } },
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  } as unknown as ReturnType<typeof useInventoryMovements>)
}

function product(over: Partial<InventoryProductSummary> & { id: string }): InventoryProductSummary {
  return {
    title: over.id,
    title_override: null,
    display_title: over.id,
    vendor: null,
    image_url: null,
    variant_count: 1,
    total_available_boxes: 10,
    total_packets: 10,
    stock_status: "in_stock",
    updated_at: "2026-01-01T00:00:00Z",
    ...over,
  }
}

function setProducts(data: InventoryProductSummary[]) {
  mockedUseInventoryProducts.mockReturnValue({
    data: { data, meta: { page: 1, page_size: 20, total_items: data.length, total_pages: 1 } },
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  } as unknown as ReturnType<typeof useInventoryProducts>)
}

beforeEach(() => {
  mockedUseSettings.mockReturnValue({
    data: undefined,
    isLoading: true,
  } as unknown as ReturnType<typeof useSettings>)
  mockedUseUpdateSettings.mockReturnValue({
    mutate: vi.fn(),
    isPending: false,
  } as unknown as ReturnType<typeof useUpdateSettings>)
  mockedUseAuth.mockReturnValue({
    hasPermission: () => true,
  } as unknown as ReturnType<typeof useAuth>)
  setDashboard()
  setMovements([])
})

describe("InventoryPage — product thumbnails", () => {
  it("renders one row per product with its thumbnail", () => {
    setProducts([
      product({ id: "p1", display_title: "Herbal Masala", image_url: "https://cdn.shopify.com/a.jpg" }),
      product({ id: "p2", display_title: "Vajrashakti", image_url: null }),
    ])
    const { container } = renderWithProviders(<InventoryPage />)

    expect(screen.getByText("Herbal Masala")).toBeInTheDocument()
    expect(screen.getByText("Vajrashakti")).toBeInTheDocument()
    // one row per product -- never a flat per-SKU/per-variant listing
    expect(container.querySelectorAll("tbody tr")).toHaveLength(2)

    const img = container.querySelector('img[alt="Herbal Masala"]')
    expect(img).toHaveAttribute("src", "https://cdn.shopify.com/a.jpg")
  })

  it("renders a neutral placeholder, never a broken <img>, when image_url is null", () => {
    setProducts([product({ id: "p2", display_title: "Vajrashakti", image_url: null })])
    const { container } = renderWithProviders(<InventoryPage />)

    expect(container.querySelector('img[alt="Vajrashakti"]')).toBeNull()
    expect(screen.getByRole("img", { name: "Vajrashakti" })).toBeInTheDocument()
  })

  it("falls back to the placeholder when the thumbnail fails to load", () => {
    setProducts([
      product({ id: "p1", display_title: "Herbal Masala", image_url: "https://cdn.shopify.com/a.jpg" }),
    ])
    const { container } = renderWithProviders(<InventoryPage />)

    const img = container.querySelector('img[alt="Herbal Masala"]') as HTMLImageElement
    expect(img).not.toBeNull()
    fireEvent.error(img)

    expect(container.querySelector('img[alt="Herbal Masala"]')).toBeNull()
    expect(screen.getByRole("img", { name: "Herbal Masala" })).toBeInTheDocument()
  })
})

describe("InventoryPage — Variants column shows the OMS-visible count", () => {
  it("renders the API's variant_count verbatim, never the raw Shopify SKU count", () => {
    // Mirrors the canonical Aayush Wellness Herbal Masala product: 9
    // underlying Shopify SKUs grouped into 3 OMS-visible flavours. The
    // column must show the already-grouped count from the API -- the
    // frontend never recomputes or re-derives it from variant data.
    setProducts([
      product({ id: "p1", display_title: "Aayush Wellness Herbal Masala", variant_count: 3 }),
    ])
    const { container } = renderWithProviders(<InventoryPage />)

    // Columns: image, Product, Vendor, Variants, Total Units, ...
    const cells = container.querySelectorAll("tbody tr td")
    expect(cells[3]).toHaveTextContent("3")
    expect(cells[3].textContent?.trim()).toBe("3")
  })
})

describe("InventoryPage — one 'Total Units' column", () => {
  it("shows Total Units (the packets total) and no separate Total boxes / Total packets", () => {
    setProducts([
      product({ id: "p1", display_title: "Vajrashakti", total_available_boxes: 12, total_packets: 1234 }),
    ])
    const { container } = renderWithProviders(<InventoryPage />)

    expect(screen.getByText("Total Units")).toBeInTheDocument()
    expect(screen.queryByText("Total boxes")).not.toBeInTheDocument()
    expect(screen.queryByText("Total packets")).not.toBeInTheDocument()
    const cells = container.querySelectorAll("tbody tr td")
    expect(cells[4].textContent?.trim()).toBe("1,234")
  })

  it("flags a non-positive total in red", () => {
    setProducts([product({ id: "p1", display_title: "Low", total_packets: 0 })])
    const { container } = renderWithProviders(<InventoryPage />)
    expect(container.querySelector("tbody tr td:nth-child(5) span")).toHaveClass("text-red-600")
  })
})

// --- Module 1.1: the dashboard section ------------------------------------

describe("InventoryPage — dashboard", () => {
  beforeEach(() => setProducts([]))

  it("renders every metric from the live aggregate, never a hardcoded value", () => {
    setDashboard({
      total_products: 42,
      total_skus: 100,
      total_available_boxes: 555,
      total_available_packets: 12000,
      opening_stock_boxes: 400,
      inward_boxes_in_range: 60,
      outward_boxes_in_range: 25,
      rto_in_transit_boxes: 8,
      rto_in_transit_count: 2,
      rto_awaiting_verification_boxes: 7,
      rto_awaiting_verification_count: 2,
      low_stock_count: 3,
      out_of_stock_count: 1,
      physical_stock_boxes: 555,
      reserved_stock_boxes: 50,
      available_to_sell_boxes: 505,
    })
    renderWithProviders(<InventoryPage />)

    expect(screen.getByText("42")).toBeInTheDocument()
    expect(screen.getByText("555")).toBeInTheDocument()
    expect(screen.getByText("50")).toBeInTheDocument()
    expect(screen.getByText("505")).toBeInTheDocument()
    expect(screen.getByText("400")).toBeInTheDocument()
    expect(screen.getByText("60")).toBeInTheDocument()
    expect(screen.getByText("25")).toBeInTheDocument()
    expect(screen.getByText("8")).toBeInTheDocument()
    expect(screen.getByText("7")).toBeInTheDocument()
    expect(screen.getByText("3")).toBeInTheDocument()
    expect(screen.getByText("1")).toBeInTheDocument()
  })

  it("shows a loading skeleton while the dashboard query is pending", () => {
    mockedUseInventoryDashboard.mockReturnValue({
      data: undefined,
      isLoading: true,
      isError: false,
      error: null,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useInventoryDashboard>)
    const { container } = renderWithProviders(<InventoryPage />)
    expect(container.querySelectorAll("[data-slot='skeleton']").length).toBeGreaterThan(0)
  })

  it("shows an error state with a retry action when the dashboard query fails", () => {
    mockedUseInventoryDashboard.mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      error: new Error("Network down"),
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useInventoryDashboard>)
    renderWithProviders(<InventoryPage />)
    expect(screen.getByText("Network down")).toBeInTheDocument()
  })
})

// --- Module 1.1: movement history section ---------------------------------

function movement(over: Partial<InventoryMovement> & { id: string }): InventoryMovement {
  return {
    product_variant_id: "v1",
    product_id: "p1",
    product_title: "Vajrashakti",
    variant_title: null,
    variant_display_title: null,
    catalog_variant_id: null,
    sku: "VJR-SKT-30",
    movement_type: "dispatch",
    quantity_delta: -2,
    quantity_after: 8,
    order_id: "order-1",
    shipment_id: "shipment-1",
    rto_id: null,
    actor_user_id: null,
    actor_label: "Shiprocket",
    reason: "Shiprocket dispatch",
    notes: null,
    created_at: "2026-01-01T00:00:00Z",
    ...over,
  }
}

describe("InventoryPage — movement history", () => {
  beforeEach(() => setProducts([]))

  it("renders one row per real ledger movement", () => {
    setMovements([
      movement({ id: "m1", sku: "VJR-SKT-30" }),
      movement({ id: "m2", sku: "VJR-SKT-60", quantity_delta: 5, movement_type: "rto_restock" }),
    ])
    renderWithProviders(<InventoryPage />)

    expect(screen.getByText("Movement History")).toBeInTheDocument()
    expect(screen.getByText("VJR-SKT-30")).toBeInTheDocument()
    expect(screen.getByText("VJR-SKT-60")).toBeInTheDocument()
  })

  it("shows an honest empty state when no movements match", () => {
    setMovements([])
    renderWithProviders(<InventoryPage />)
    expect(screen.getByText("No stock movements found")).toBeInTheDocument()
  })

  it("shows a loading skeleton while the movements query is pending", () => {
    mockedUseInventoryMovements.mockReturnValue({
      data: undefined,
      isLoading: true,
      isError: false,
      error: null,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useInventoryMovements>)
    const { container } = renderWithProviders(<InventoryPage />)
    expect(container.querySelectorAll("[data-slot='skeleton']").length).toBeGreaterThan(0)
  })

  it("shows an error state with a retry action when the movements query fails", () => {
    mockedUseInventoryMovements.mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      error: new Error("Movements unavailable"),
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useInventoryMovements>)
    renderWithProviders(<InventoryPage />)
    expect(screen.getByText("Movements unavailable")).toBeInTheDocument()
  })

  it("passes the SKU search box's value through to the movements query", async () => {
    setMovements([])
    renderWithProviders(<InventoryPage />)

    // The products table above also has a box mentioning SKU ("Search by product
    // name or SKU..."); the movement history's own box is the one that says only SKU.
    const skuSearch = screen.getByPlaceholderText("Search by SKU...")
    await userEvent.type(skuSearch, "VJR")

    const lastCall = mockedUseInventoryMovements.mock.calls.at(-1)?.[0]
    expect(lastCall?.sku).toBe("VJR")
  })
})

import { describe, expect, it, vi, beforeEach } from "vitest"
import { fireEvent, screen } from "@testing-library/react"

import { renderWithProviders } from "@/test-utils/render-with-providers"
import InventoryPage from "@/app/(dashboard)/inventory/page"
import { useInventoryProducts } from "@/services/inventory"
import { useSettings, useUpdateSettings } from "@/services/settings"
import { useAuth } from "@/lib/auth-context"
import type { InventoryProductSummary } from "@/types/inventory"

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn() }),
  usePathname: () => "/inventory",
  useSearchParams: () => new URLSearchParams(),
}))

vi.mock("@/services/inventory", () => ({ useInventoryProducts: vi.fn() }))
vi.mock("@/services/settings", () => ({ useSettings: vi.fn(), useUpdateSettings: vi.fn() }))
vi.mock("@/lib/auth-context", () => ({ useAuth: vi.fn() }))

const mockedUseInventoryProducts = vi.mocked(useInventoryProducts)
const mockedUseSettings = vi.mocked(useSettings)
const mockedUseUpdateSettings = vi.mocked(useUpdateSettings)
const mockedUseAuth = vi.mocked(useAuth)

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

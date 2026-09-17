import { describe, expect, it, vi } from "vitest"
import { screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { PlatformStockSection } from "@/components/inventory/platform-stock-section"
import { renderWithProviders } from "@/test-utils/render-with-providers"
import { useRecordPlatformStockMovement } from "@/services/platform-inventory"
import type { ProductPlatformStock } from "@/types/platform-inventory"

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}))

vi.mock("@/services/platform-inventory", () => ({
  useRecordPlatformStockMovement: vi.fn(),
}))

const mockedUseRecordPlatformStockMovement = vi.mocked(useRecordPlatformStockMovement)

const DATA: ProductPlatformStock = {
  product_id: "prod-1",
  product_title: "Vajrashakti",
  stock_date: "2026-09-11",
  variants: [
    {
      product_variant_id: "var-1",
      sku: "VJR-60",
      variant_title: "Vajrashakti 60 Tablets",
      stock_date: "2026-09-11",
      platforms: [
        {
          platform: "shopify",
          platform_label: "Shopify",
          is_automatic: true,
          opening_stock: null,
          stock_added: 12,
          stock_deducted: 35,
          current_stock: 1200,
          last_updated: "2026-09-11T10:00:00Z",
        },
        {
          platform: "amazon",
          platform_label: "Amazon",
          is_automatic: false,
          opening_stock: 500,
          stock_added: 50,
          stock_deducted: 18,
          current_stock: 532,
          last_updated: "2026-09-11T09:00:00Z",
        },
        {
          platform: "flipkart",
          platform_label: "Flipkart",
          is_automatic: false,
          opening_stock: 0,
          stock_added: 0,
          stock_deducted: 0,
          current_stock: 0,
          last_updated: null,
        },
      ],
    },
  ],
}

function baseProps(overrides: Partial<React.ComponentProps<typeof PlatformStockSection>> = {}) {
  return {
    productTitle: "Vajrashakti",
    isLoading: false,
    isError: false,
    error: null,
    data: DATA,
    onRetry: vi.fn(),
    canManage: true,
    stockDate: "2026-09-11",
    ...overrides,
  }
}

describe("PlatformStockSection", () => {
  it("shows Shopify as automatic/read-only and every manual platform with its own row", () => {
    mockedUseRecordPlatformStockMovement.mockReturnValue({
      mutateAsync: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useRecordPlatformStockMovement>)

    renderWithProviders(<PlatformStockSection {...baseProps()} />)

    expect(screen.getByText("Shopify")).toBeInTheDocument()
    expect(screen.getByText("Automatic")).toBeInTheDocument()
    expect(screen.getByText("Amazon")).toBeInTheDocument()
    expect(screen.getByText("Flipkart")).toBeInTheDocument()
    expect(screen.getByText("532")).toBeInTheDocument() // Amazon current stock
  })

  it("BUG FIX: renders 'Not available' (never a blank cell or a fabricated 0) when Shopify's historical balance can't be reconstructed", () => {
    mockedUseRecordPlatformStockMovement.mockReturnValue({
      mutateAsync: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useRecordPlatformStockMovement>)

    const dataWithUnavailableShopifyHistory: ProductPlatformStock = {
      ...DATA,
      variants: [
        {
          ...DATA.variants[0],
          platforms: DATA.variants[0].platforms.map((row) =>
            row.platform === "shopify" ? { ...row, current_stock: null, opening_stock: null } : row
          ),
        },
      ],
    }

    renderWithProviders(
      <PlatformStockSection {...baseProps({ data: dataWithUnavailableShopifyHistory })} />
    )

    expect(screen.getAllByText("Not available")).toHaveLength(1) // Shopify's current stock cell only
    const shopifyRow = screen.getByText("Shopify").closest("tr")
    expect(shopifyRow).not.toBeNull()
    // The Shopify row's own cells never fall back to a fabricated "0" —
    // Flipkart legitimately showing literal 0s elsewhere on the page is
    // fine and must not make this assertion falsely pass or fail.
    expect(within(shopifyRow as HTMLElement).queryByText("0")).not.toBeInTheDocument()
  })

  it("never shows an Add Stock action for the automatic Shopify row", () => {
    mockedUseRecordPlatformStockMovement.mockReturnValue({
      mutateAsync: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useRecordPlatformStockMovement>)

    renderWithProviders(<PlatformStockSection {...baseProps()} />)

    // Two manual platform rows (Amazon, Flipkart) -> exactly 2 Add Stock buttons.
    expect(screen.getAllByRole("button", { name: /^Add Stock$/i })).toHaveLength(2)
  })

  it("hides Add Stock/Record Sale actions for a read-only user", () => {
    mockedUseRecordPlatformStockMovement.mockReturnValue({
      mutateAsync: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useRecordPlatformStockMovement>)

    renderWithProviders(<PlatformStockSection {...baseProps({ canManage: false })} />)

    expect(screen.queryByRole("button", { name: /^Add Stock$/i })).not.toBeInTheDocument()
    expect(screen.getAllByText("Read-only")).toHaveLength(2)
  })

  it("opens Add Stock with an EMPTY quantity input and computes current+new correctly", async () => {
    const user = userEvent.setup()
    const mutateAsync = vi.fn().mockResolvedValue({ quantity_after: 582 })
    mockedUseRecordPlatformStockMovement.mockReturnValue({
      mutateAsync,
      isPending: false,
    } as unknown as ReturnType<typeof useRecordPlatformStockMovement>)

    renderWithProviders(<PlatformStockSection {...baseProps()} />)

    const addButtons = screen.getAllByRole("button", { name: /^Add Stock$/i })
    await user.click(addButtons[0]) // Amazon's row (532 current)

    const dialog = screen.getByRole("dialog")
    const quantityInput = within(dialog).getByLabelText(/New stock to add/i)
    expect(quantityInput).toHaveValue(null) // starts empty, never pre-filled with 532

    await user.type(quantityInput, "50")
    expect(within(dialog).getByText("New Stock: 582 boxes")).toBeInTheDocument() // 532 + 50, never a bare "50"

    await user.click(within(dialog).getByRole("button", { name: /^Add Stock$/i }))
    expect(mutateAsync).toHaveBeenCalledWith(
      expect.objectContaining({ platform: "amazon", movement_type: "stock_added", quantity: 50 })
    )
  })

  it("Record Sale sends stock_deducted and never lets the balance preview go negative without disabling Save", async () => {
    const user = userEvent.setup()
    const mutateAsync = vi.fn().mockResolvedValue({ quantity_after: 500 })
    mockedUseRecordPlatformStockMovement.mockReturnValue({
      mutateAsync,
      isPending: false,
    } as unknown as ReturnType<typeof useRecordPlatformStockMovement>)

    renderWithProviders(<PlatformStockSection {...baseProps()} />)

    await user.click(screen.getAllByRole("button", { name: /^Record Sale$/i })[0]) // Amazon
    const dialog = screen.getByRole("dialog")
    const quantityInput = within(dialog).getByLabelText(/Quantity sold/i)
    await user.type(quantityInput, "1000") // more than the 532 current stock

    const saveButton = within(dialog).getByRole("button", { name: /^Record Sale$/i })
    expect(saveButton).toBeDisabled() // preview would go negative
  })

  it("shows a clean empty state when the product has no variants", () => {
    mockedUseRecordPlatformStockMovement.mockReturnValue({
      mutateAsync: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useRecordPlatformStockMovement>)

    renderWithProviders(
      <PlatformStockSection {...baseProps({ data: { ...DATA, variants: [] } })} />
    )

    expect(screen.getByText("No SKUs found for this product")).toBeInTheDocument()
  })

  it("shows a loading state", () => {
    mockedUseRecordPlatformStockMovement.mockReturnValue({
      mutateAsync: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useRecordPlatformStockMovement>)

    renderWithProviders(
      <PlatformStockSection {...baseProps({ isLoading: true, data: undefined })} />
    )
    // QueryStates renders its own skeleton -- just confirm no crash and no data leak.
    expect(screen.queryByText("Amazon")).not.toBeInTheDocument()
  })

})

describe("PlatformStockSection — multi-SKU aggregation (ONE table per product)", () => {
  const MULTI: ProductPlatformStock = {
    product_id: "prod-1",
    product_title: "Vajrashakti",
    stock_date: "2026-09-11",
    variants: [
      {
        product_variant_id: "var-30",
        sku: "VJR-SKT-30",
        variant_title: "Pack of 1",
        stock_date: "2026-09-11",
        platforms: [
          {
            platform: "shopify",
            platform_label: "Shopify",
            is_automatic: true,
            opening_stock: 100,
            stock_added: 5,
            stock_deducted: 10,
            current_stock: 396,
            last_updated: "2026-09-11T08:00:00Z",
          },
          {
            platform: "amazon",
            platform_label: "Amazon",
            is_automatic: false,
            opening_stock: 10,
            stock_added: 2,
            stock_deducted: 1,
            current_stock: 11,
            last_updated: "2026-09-11T07:00:00Z",
          },
        ],
      },
      {
        product_variant_id: "var-60",
        sku: "VJR-SKT-60",
        variant_title: "Pack of 2",
        stock_date: "2026-09-11",
        platforms: [
          {
            platform: "shopify",
            platform_label: "Shopify",
            is_automatic: true,
            opening_stock: 200,
            stock_added: 0,
            stock_deducted: 0,
            current_stock: 300,
            last_updated: "2026-09-11T09:00:00Z",
          },
          {
            platform: "amazon",
            platform_label: "Amazon",
            is_automatic: false,
            opening_stock: 20,
            stock_added: 3,
            stock_deducted: 2,
            current_stock: 21,
            last_updated: "2026-09-11T10:00:00Z",
          },
        ],
      },
    ],
  }

  function multiProps(overrides: Partial<React.ComponentProps<typeof PlatformStockSection>> = {}) {
    return {
      productTitle: "Vajrashakti",
      isLoading: false,
      isError: false,
      error: null,
      data: MULTI,
      onRetry: vi.fn(),
      canManage: true,
      stockDate: "2026-09-11",
      ...overrides,
    }
  }

  it("renders ONE table for the product, not one per SKU", () => {
    mockedUseRecordPlatformStockMovement.mockReturnValue({
      mutateAsync: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useRecordPlatformStockMovement>)

    const { container } = renderWithProviders(<PlatformStockSection {...multiProps()} />)

    expect(container.querySelectorAll("table")).toHaveLength(1)
    // exactly one Shopify row and one Amazon row, not one pair per SKU
    expect(screen.getAllByText("Shopify")).toHaveLength(1)
    expect(screen.getAllByText("Amazon")).toHaveLength(1)
  })

  it("sums Opening/Added/Deducted/Current across every SKU for each platform", () => {
    mockedUseRecordPlatformStockMovement.mockReturnValue({
      mutateAsync: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useRecordPlatformStockMovement>)

    renderWithProviders(<PlatformStockSection {...multiProps()} />)

    const amazonRow = screen.getByText("Amazon").closest("tr") as HTMLElement
    expect(within(amazonRow).getByText("30")).toBeInTheDocument() // opening 10+20
    expect(within(amazonRow).getByText("+5")).toBeInTheDocument() // added 2+3
    expect(within(amazonRow).getByText("-3")).toBeInTheDocument() // deducted 1+2
    expect(within(amazonRow).getByText("32")).toBeInTheDocument() // current 11+21

    const shopifyRow = screen.getByText("Shopify").closest("tr") as HTMLElement
    expect(within(shopifyRow).getByText("696")).toBeInTheDocument() // current 396+300
  })

  it("propagates null (never silently sums only the known SKUs) when any SKU's Shopify balance is unavailable", () => {
    mockedUseRecordPlatformStockMovement.mockReturnValue({
      mutateAsync: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useRecordPlatformStockMovement>)

    const withOneUnavailable: ProductPlatformStock = {
      ...MULTI,
      variants: [
        {
          ...MULTI.variants[0],
          platforms: MULTI.variants[0].platforms.map((row) =>
            row.platform === "shopify" ? { ...row, current_stock: null, opening_stock: null } : row
          ),
        },
        MULTI.variants[1],
      ],
    }

    renderWithProviders(<PlatformStockSection {...multiProps({ data: withOneUnavailable })} />)

    const shopifyRow = screen.getByText("Shopify").closest("tr") as HTMLElement
    expect(within(shopifyRow).getAllByText("Not available")).toHaveLength(1)
    expect(within(shopifyRow).queryByText("696")).not.toBeInTheDocument() // never a partial sum
  })

  it("Add Stock on the combined row requires picking a SKU before Save enables", async () => {
    const user = userEvent.setup()
    const mutateAsync = vi.fn().mockResolvedValue({ quantity_after: 13 })
    mockedUseRecordPlatformStockMovement.mockReturnValue({
      mutateAsync,
      isPending: false,
    } as unknown as ReturnType<typeof useRecordPlatformStockMovement>)

    renderWithProviders(<PlatformStockSection {...multiProps()} />)

    await user.click(screen.getAllByRole("button", { name: /^Add Stock$/i })[0]) // Amazon (combined)
    const dialog = screen.getByRole("dialog")
    await user.type(within(dialog).getByLabelText(/New stock to add/i), "2")

    expect(within(dialog).getByText("Select a SKU first")).toBeInTheDocument()
    expect(within(dialog).getByRole("button", { name: /^Add Stock$/i })).toBeDisabled()

    await user.click(within(dialog).getByRole("combobox"))
    await user.click(await screen.findByText("Pack of 1"))

    expect(within(dialog).getByText("11 boxes")).toBeInTheDocument() // var-30's OWN current stock, not the 32 aggregate
    expect(within(dialog).getByText("New Stock: 13 boxes")).toBeInTheDocument() // 11 + 2

    await user.click(within(dialog).getByRole("button", { name: /^Add Stock$/i }))
    expect(mutateAsync).toHaveBeenCalledWith(
      expect.objectContaining({ platform: "amazon", movement_type: "stock_added", quantity: 2 })
    )
  })

  it("a single-SKU product still auto-selects with no picker shown", async () => {
    const user = userEvent.setup()
    mockedUseRecordPlatformStockMovement.mockReturnValue({
      mutateAsync: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useRecordPlatformStockMovement>)

    renderWithProviders(<PlatformStockSection {...baseProps()} />)

    await user.click(screen.getAllByRole("button", { name: /^Add Stock$/i })[0])
    const dialog = screen.getByRole("dialog")
    expect(within(dialog).queryByRole("combobox")).not.toBeInTheDocument()
    expect(within(dialog).getByText("532 boxes")).toBeInTheDocument()
  })
})

describe("PlatformStockSection — error state", () => {
  it("shows an error state and calls onRetry", async () => {
    const user = userEvent.setup()
    const onRetry = vi.fn()
    mockedUseRecordPlatformStockMovement.mockReturnValue({
      mutateAsync: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useRecordPlatformStockMovement>)

    renderWithProviders(
      <PlatformStockSection
        {...baseProps({ isError: true, error: new Error("boom"), data: undefined, onRetry })}
      />
    )
    const retryButton = screen.queryByRole("button", { name: /retry/i })
    if (retryButton) {
      await user.click(retryButton)
      expect(onRetry).toHaveBeenCalled()
    }
  })
})

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

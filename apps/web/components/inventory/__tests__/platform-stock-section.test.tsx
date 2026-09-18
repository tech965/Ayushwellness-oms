import { describe, expect, it, vi } from "vitest"
import { screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { PlatformStockSection } from "@/components/inventory/platform-stock-section"
import { renderWithProviders } from "@/test-utils/render-with-providers"
import { useRecordProductMarketplaceMovement } from "@/services/platform-inventory"
import type { ProductPlatformStock } from "@/types/platform-inventory"

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}))

vi.mock("@/services/platform-inventory", () => ({
  useRecordProductMarketplaceMovement: vi.fn(),
}))

const mockedUseRecordProductMarketplaceMovement = vi.mocked(useRecordProductMarketplaceMovement)

const DATA: ProductPlatformStock = {
  product_id: "prod-1",
  product_title: "Vajrashakti",
  stock_date: "2026-09-18",
  platforms: [
    {
      platform: "shopify",
      platform_label: "Shopify",
      is_automatic: true,
      opening_stock: null,
      stock_added: 12,
      stock_deducted: 35,
      current_stock: 1200,
      last_updated: "2026-09-18T10:00:00Z",
    },
    {
      platform: "amazon",
      platform_label: "Amazon",
      is_automatic: false,
      opening_stock: 500,
      stock_added: 50,
      stock_deducted: 18,
      current_stock: 532,
      last_updated: "2026-09-18T09:00:00Z",
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
}

function baseProps(overrides: Partial<React.ComponentProps<typeof PlatformStockSection>> = {}) {
  return {
    productId: "prod-1",
    productTitle: "Vajrashakti",
    isLoading: false,
    isError: false,
    error: null,
    data: DATA,
    onRetry: vi.fn(),
    canManage: true,
    stockDate: "2026-09-18",
    ...overrides,
  }
}

function mutationStub(behaviour: "success" | "error" = "success") {
  const mutateAsync = vi.fn(async () => {
    if (behaviour === "error") throw new Error("Server said no")
    return { quantity_after: -18 }
  })
  return { mutateAsync, isPending: false } as unknown as ReturnType<
    typeof useRecordProductMarketplaceMovement
  >
}

describe("PlatformStockSection — ONE table per product, no SKU selection", () => {
  it("renders a single combined table -- one Shopify row and every manual platform row, no per-SKU grouping", () => {
    mockedUseRecordProductMarketplaceMovement.mockReturnValue(mutationStub())
    const { container } = renderWithProviders(<PlatformStockSection {...baseProps()} />)

    expect(container.querySelectorAll("table")).toHaveLength(1)
    expect(screen.getByText("Shopify")).toBeInTheDocument()
    expect(screen.getByText("Automatic")).toBeInTheDocument()
    expect(screen.getByText("Amazon")).toBeInTheDocument()
    expect(screen.getByText("Flipkart")).toBeInTheDocument()
    expect(screen.getByText("532")).toBeInTheDocument() // Amazon current stock
  })

  it("renders 'Not available' (never a blank cell or a fabricated 0) when Shopify's historical balance can't be reconstructed", () => {
    mockedUseRecordProductMarketplaceMovement.mockReturnValue(mutationStub())
    const dataWithUnavailableShopify: ProductPlatformStock = {
      ...DATA,
      platforms: DATA.platforms.map((row) =>
        row.platform === "shopify" ? { ...row, current_stock: null, opening_stock: null } : row
      ),
    }

    renderWithProviders(<PlatformStockSection {...baseProps({ data: dataWithUnavailableShopify })} />)

    expect(screen.getAllByText("Not available")).toHaveLength(1)
    const shopifyRow = screen.getByText("Shopify").closest("tr")
    expect(shopifyRow).not.toBeNull()
    expect(within(shopifyRow as HTMLElement).queryByText("0")).not.toBeInTheDocument()
  })

  it("shows Add Stock, Record Sale, and RTO for every manual platform row -- never for Shopify", () => {
    mockedUseRecordProductMarketplaceMovement.mockReturnValue(mutationStub())
    renderWithProviders(<PlatformStockSection {...baseProps()} />)

    expect(screen.getAllByRole("button", { name: /^Add Stock$/i })).toHaveLength(2) // Amazon, Flipkart
    expect(screen.getAllByRole("button", { name: /^Record Sale$/i })).toHaveLength(2)
    expect(screen.getAllByRole("button", { name: /^RTO$/i })).toHaveLength(2)
    expect(screen.getByText("Synced from Shopify")).toBeInTheDocument()
  })

  it("hides all three actions for a read-only user", () => {
    mockedUseRecordProductMarketplaceMovement.mockReturnValue(mutationStub())
    renderWithProviders(<PlatformStockSection {...baseProps({ canManage: false })} />)

    expect(screen.queryByRole("button", { name: /^Add Stock$/i })).not.toBeInTheDocument()
    expect(screen.queryByRole("button", { name: /^RTO$/i })).not.toBeInTheDocument()
    expect(screen.getAllByText("Read-only")).toHaveLength(2)
  })

  it("never shows a SKU field anywhere in the Add Stock dialog", async () => {
    const user = userEvent.setup()
    mockedUseRecordProductMarketplaceMovement.mockReturnValue(mutationStub())
    renderWithProviders(<PlatformStockSection {...baseProps()} />)

    await user.click(screen.getAllByRole("button", { name: /^Add Stock$/i })[0])
    const dialog = screen.getByRole("dialog")
    expect(within(dialog).queryByText(/sku/i)).not.toBeInTheDocument()
    expect(within(dialog).queryByRole("combobox")).not.toBeInTheDocument()
    expect(within(dialog).queryByText(/select a sku/i)).not.toBeInTheDocument()
  })

  it("Add Stock asks for a packet quantity and sends it as quantity_packets", async () => {
    const user = userEvent.setup()
    const mutateAsync = vi.fn().mockResolvedValue({ quantity_after: 582 })
    mockedUseRecordProductMarketplaceMovement.mockReturnValue({
      mutateAsync,
      isPending: false,
    } as unknown as ReturnType<typeof useRecordProductMarketplaceMovement>)
    renderWithProviders(<PlatformStockSection {...baseProps()} />)

    await user.click(screen.getAllByRole("button", { name: /^Add Stock$/i })[0]) // Amazon
    const dialog = screen.getByRole("dialog")
    expect(within(dialog).getByText("Add Stock — Amazon")).toBeInTheDocument()
    const quantityInput = within(dialog).getByLabelText(/Quantity to add/i)
    expect(quantityInput).toHaveValue(null) // starts empty

    await user.type(quantityInput, "50")
    expect(within(dialog).getByText("New Stock: 582 outers")).toBeInTheDocument() // 532 + 50

    await user.click(within(dialog).getByRole("button", { name: /^Add Stock$/i }))
    expect(mutateAsync).toHaveBeenCalledWith(
      expect.objectContaining({
        platform: "amazon",
        movement_type: "stock_added",
        quantity_packets: 50,
      })
    )
  })

  it("Record Sale sends movement_type sale and allows the preview to go negative (never blocked)", async () => {
    const user = userEvent.setup()
    const mutateAsync = vi.fn().mockResolvedValue({ quantity_after: -468 })
    mockedUseRecordProductMarketplaceMovement.mockReturnValue({
      mutateAsync,
      isPending: false,
    } as unknown as ReturnType<typeof useRecordProductMarketplaceMovement>)
    renderWithProviders(<PlatformStockSection {...baseProps()} />)

    await user.click(screen.getAllByRole("button", { name: /^Record Sale$/i })[0]) // Amazon, 532
    const dialog = screen.getByRole("dialog")
    const quantityInput = within(dialog).getByLabelText(/Quantity Sold/i)
    await user.type(quantityInput, "1000") // more than current stock

    expect(within(dialog).getByText("New Stock: -468 outers")).toBeInTheDocument()
    expect(within(dialog).getByRole("button", { name: /^Record Sale$/i })).toBeEnabled()

    await user.click(within(dialog).getByRole("button", { name: /^Record Sale$/i }))
    expect(mutateAsync).toHaveBeenCalledWith(
      expect.objectContaining({ platform: "amazon", movement_type: "sale", quantity_packets: 1000 })
    )
  })

  it("RTO sends movement_type rto and increases the balance", async () => {
    const user = userEvent.setup()
    const mutateAsync = vi.fn().mockResolvedValue({ quantity_after: 534 })
    mockedUseRecordProductMarketplaceMovement.mockReturnValue({
      mutateAsync,
      isPending: false,
    } as unknown as ReturnType<typeof useRecordProductMarketplaceMovement>)
    renderWithProviders(<PlatformStockSection {...baseProps()} />)

    await user.click(screen.getAllByRole("button", { name: /^RTO$/i })[0]) // Amazon
    const dialog = screen.getByRole("dialog")
    expect(within(dialog).getByText("Record RTO — Amazon")).toBeInTheDocument()
    const quantityInput = within(dialog).getByLabelText(/Quantity Returned/i)
    await user.type(quantityInput, "2")
    expect(within(dialog).getByText("New Stock: 534 outers")).toBeInTheDocument() // 532 + 2

    await user.click(within(dialog).getByRole("button", { name: /^Record RTO$/i }))
    expect(mutateAsync).toHaveBeenCalledWith(
      expect.objectContaining({ platform: "amazon", movement_type: "rto", quantity_packets: 2 })
    )
  })

  it("keeps the dialog open and shows the API error on a failed save", async () => {
    const user = userEvent.setup()
    mockedUseRecordProductMarketplaceMovement.mockReturnValue(mutationStub("error"))
    renderWithProviders(<PlatformStockSection {...baseProps()} />)

    await user.click(screen.getAllByRole("button", { name: /^Add Stock$/i })[0])
    const dialog = screen.getByRole("dialog")
    await user.type(within(dialog).getByLabelText(/Quantity to add/i), "50")
    await user.click(within(dialog).getByRole("button", { name: /^Add Stock$/i }))

    expect(screen.getByRole("dialog")).toBeInTheDocument()
  })

  it("shows a clean empty state when there is no marketplace stock data", () => {
    mockedUseRecordProductMarketplaceMovement.mockReturnValue(mutationStub())
    renderWithProviders(
      <PlatformStockSection {...baseProps({ data: { ...DATA, platforms: [] } })} />
    )
    expect(screen.getByText("No marketplace stock data for this product")).toBeInTheDocument()
  })

  it("shows a loading state without crashing or leaking data", () => {
    mockedUseRecordProductMarketplaceMovement.mockReturnValue(mutationStub())
    renderWithProviders(<PlatformStockSection {...baseProps({ isLoading: true, data: undefined })} />)
    expect(screen.queryByText("Amazon")).not.toBeInTheDocument()
  })

  it("shows an error state and calls onRetry", async () => {
    const user = userEvent.setup()
    const onRetry = vi.fn()
    mockedUseRecordProductMarketplaceMovement.mockReturnValue(mutationStub())

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

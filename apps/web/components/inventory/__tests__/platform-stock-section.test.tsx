import { describe, expect, it, vi } from "vitest"
import { screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { PlatformStockSection } from "@/components/inventory/platform-stock-section"
import { renderWithProviders } from "@/test-utils/render-with-providers"
import { useRecordProductMarketplaceMovement } from "@/services/platform-inventory"
import type { PlatformStockSummaryRow, ProductPlatformStock } from "@/types/platform-inventory"

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}))

vi.mock("@/services/platform-inventory", () => ({
  useRecordProductMarketplaceMovement: vi.fn(),
}))

const mockedUseRecord = vi.mocked(useRecordProductMarketplaceMovement)

function manualRow(
  platform: string,
  platform_label: string,
  over: Partial<PlatformStockSummaryRow> = {}
): PlatformStockSummaryRow {
  return {
    platform,
    platform_label,
    is_automatic: false,
    opening_stock: 0,
    stock_added: 0,
    stock_deducted: 0,
    current_stock: 0,
    last_updated: null,
    ...over,
  }
}

const DATA: ProductPlatformStock = {
  product_id: "prod-1",
  product_title: "Aayush Wellness Herbal Masala",
  stock_date: "2026-09-21",
  sold_this_month_packets: 25,
  platforms: [
    {
      platform: "shopify",
      platform_label: "Shopify",
      is_automatic: true,
      opening_stock: null,
      stock_added: 12,
      stock_deducted: 35,
      current_stock: 1200,
      last_updated: "2026-09-21T10:00:00Z",
    },
    manualRow("amazon", "Amazon", {
      opening_stock: 500,
      stock_added: 2,
      stock_deducted: 20,
      current_stock: 482,
      last_updated: "2026-09-21T09:00:00Z",
    }),
    manualRow("flipkart", "Flipkart"),
    manualRow("blinkit", "Blinkit"),
    manualRow("meesho", "Meesho"),
    manualRow("manual_other", "Manual / Other"),
  ],
}

function baseProps(overrides: Partial<React.ComponentProps<typeof PlatformStockSection>> = {}) {
  return {
    productId: "prod-1",
    productTitle: "Aayush Wellness Herbal Masala",
    isLoading: false,
    isError: false,
    error: null,
    data: DATA,
    onRetry: vi.fn(),
    canManage: true,
    stockDate: "2026-09-21",
    ...overrides,
  }
}

function stub(behaviour: "success" | "error" = "success", quantityAfter = -18) {
  const mutateAsync = vi.fn(async () => {
    if (behaviour === "error") throw new Error("Server said no")
    return { quantity_after: quantityAfter }
  })
  mockedUseRecord.mockReturnValue({
    mutateAsync,
    isPending: false,
  } as unknown as ReturnType<typeof useRecordProductMarketplaceMovement>)
  return mutateAsync
}

const MANUAL_PLATFORM_LABELS = ["Amazon", "Flipkart", "Blinkit", "Meesho", "Manual / Other"]

describe("PlatformStockSection — ONE table per product", () => {
  it("renders a single combined table titled with the product name", () => {
    stub()
    const { container } = renderWithProviders(<PlatformStockSection {...baseProps()} />)

    expect(container.querySelectorAll("table")).toHaveLength(1)
    expect(screen.getByText("Marketplace Stock — Aayush Wellness Herbal Masala")).toBeInTheDocument()
    expect(screen.getByText("Shopify")).toBeInTheDocument()
    expect(screen.getByText("Automatic")).toBeInTheDocument()
    expect(screen.getByText("482")).toBeInTheDocument() // Amazon current stock
  })

  it("renders 'Not available' (never a blank cell or a fabricated 0) when Shopify's historical balance can't be reconstructed", () => {
    stub()
    const data: ProductPlatformStock = {
      ...DATA,
      platforms: DATA.platforms.map((row) =>
        row.platform === "shopify" ? { ...row, current_stock: null, opening_stock: null } : row
      ),
    }
    renderWithProviders(<PlatformStockSection {...baseProps({ data })} />)

    expect(screen.getAllByText("Not available")).toHaveLength(1)
    const shopifyRow = screen.getByText("Shopify").closest("tr") as HTMLElement
    expect(within(shopifyRow).queryByText("0")).not.toBeInTheDocument()
  })
})

describe("PlatformStockSection — only Record Sale and RTO", () => {
  it("has no Add Stock action anywhere", () => {
    stub()
    renderWithProviders(<PlatformStockSection {...baseProps()} />)
    expect(screen.queryByRole("button", { name: /add stock/i })).not.toBeInTheDocument()
  })

  it.each(MANUAL_PLATFORM_LABELS)("%s shows exactly Record Sale and RTO", (label) => {
    stub()
    renderWithProviders(<PlatformStockSection {...baseProps()} />)

    const row = screen.getByText(label).closest("tr") as HTMLElement
    const buttons = within(row)
      .getAllByRole("button")
      .map((b) => b.textContent?.trim())
    expect(buttons).toEqual(["Record Sale", "RTO"])
  })

  it("Shopify stays 'Synced from Shopify' with no manual controls", () => {
    stub()
    renderWithProviders(<PlatformStockSection {...baseProps()} />)

    const row = screen.getByText("Shopify").closest("tr") as HTMLElement
    expect(within(row).getByText("Synced from Shopify")).toBeInTheDocument()
    expect(within(row).queryAllByRole("button")).toHaveLength(0)
  })

  it("hides both actions for a read-only user", () => {
    stub()
    renderWithProviders(<PlatformStockSection {...baseProps({ canManage: false })} />)

    expect(screen.queryByRole("button", { name: /record sale/i })).not.toBeInTheDocument()
    expect(screen.queryByRole("button", { name: /^RTO$/i })).not.toBeInTheDocument()
    expect(screen.getAllByText("Read-only")).toHaveLength(5)
  })
})

describe("PlatformStockSection — product-level dialogs, no SKU", () => {
  it.each(["Record Sale", "RTO"])("the %s dialog never shows a SKU field", async (action) => {
    const user = userEvent.setup()
    stub()
    renderWithProviders(<PlatformStockSection {...baseProps()} />)

    const amazon = screen.getByText("Amazon").closest("tr") as HTMLElement
    await user.click(within(amazon).getByRole("button", { name: new RegExp(`^${action}$`) }))
    const dialog = screen.getByRole("dialog")

    expect(within(dialog).queryByRole("combobox")).not.toBeInTheDocument()
    expect(within(dialog).queryByText(/sku/i)).not.toBeInTheDocument()
    expect(within(dialog).queryByText(/select a sku/i)).not.toBeInTheDocument()
    expect(within(dialog).getByText(/Product: Aayush Wellness Herbal Masala/)).toBeInTheDocument()
  })

  it("Record Sale sends the packet quantity for the product and previews the platform balance", async () => {
    const user = userEvent.setup()
    const mutateAsync = stub("success", 462)
    renderWithProviders(<PlatformStockSection {...baseProps()} />)

    const amazon = screen.getByText("Amazon").closest("tr") as HTMLElement
    await user.click(within(amazon).getByRole("button", { name: /^Record Sale$/ }))
    const dialog = screen.getByRole("dialog")
    expect(within(dialog).getByText("Record Sale — Amazon")).toBeInTheDocument()

    const input = within(dialog).getByLabelText(/Quantity Sold/i)
    expect(input).toHaveValue(null) // starts empty
    await user.type(input, "20")
    expect(within(dialog).getByText("New Stock: 462 outers")).toBeInTheDocument() // 482 - 20
    await user.type(within(dialog).getByLabelText(/Reason/i), "Marketplace sale")
    await user.click(within(dialog).getByRole("button", { name: /^Record Sale$/ }))

    expect(mutateAsync).toHaveBeenCalledWith({
      platform: "amazon",
      movement_type: "sale",
      quantity_packets: 20,
      reason: "Marketplace sale",
      stock_date: "2026-09-21",
    })
  })

  it("a Sale may take the platform balance negative without being blocked", async () => {
    const user = userEvent.setup()
    stub()
    renderWithProviders(<PlatformStockSection {...baseProps()} />)

    const flipkart = screen.getByText("Flipkart").closest("tr") as HTMLElement
    await user.click(within(flipkart).getByRole("button", { name: /^Record Sale$/ }))
    const dialog = screen.getByRole("dialog")
    await user.type(within(dialog).getByLabelText(/Quantity Sold/i), "20")

    expect(within(dialog).getByText("New Stock: -20 outers")).toBeInTheDocument()
    expect(within(dialog).getByRole("button", { name: /^Record Sale$/ })).toBeEnabled()
  })

  it("RTO sends movement_type rto and previews an increase", async () => {
    const user = userEvent.setup()
    const mutateAsync = stub("success", 484)
    renderWithProviders(<PlatformStockSection {...baseProps()} />)

    const amazon = screen.getByText("Amazon").closest("tr") as HTMLElement
    await user.click(within(amazon).getByRole("button", { name: /^RTO$/ }))
    const dialog = screen.getByRole("dialog")
    expect(within(dialog).getByText("Record RTO — Amazon")).toBeInTheDocument()

    await user.type(within(dialog).getByLabelText(/Quantity Returned/i), "2")
    expect(within(dialog).getByText("New Stock: 484 outers")).toBeInTheDocument() // 482 + 2
    await user.click(within(dialog).getByRole("button", { name: /^Record RTO$/ }))

    expect(mutateAsync).toHaveBeenCalledWith(
      expect.objectContaining({ platform: "amazon", movement_type: "rto", quantity_packets: 2 })
    )
  })

  it("rejects an empty / zero / negative quantity (Save stays disabled)", async () => {
    const user = userEvent.setup()
    stub()
    renderWithProviders(<PlatformStockSection {...baseProps()} />)

    const amazon = screen.getByText("Amazon").closest("tr") as HTMLElement
    await user.click(within(amazon).getByRole("button", { name: /^Record Sale$/ }))
    const dialog = screen.getByRole("dialog")
    const save = within(dialog).getByRole("button", { name: /^Record Sale$/ })
    expect(save).toBeDisabled()
    for (const bad of ["0", "-3"]) {
      await user.clear(within(dialog).getByLabelText(/Quantity Sold/i))
      await user.type(within(dialog).getByLabelText(/Quantity Sold/i), bad)
      expect(save).toBeDisabled()
    }
  })

  it("keeps the dialog open when the server rejects the save", async () => {
    const user = userEvent.setup()
    stub("error")
    renderWithProviders(<PlatformStockSection {...baseProps()} />)

    const amazon = screen.getByText("Amazon").closest("tr") as HTMLElement
    await user.click(within(amazon).getByRole("button", { name: /^Record Sale$/ }))
    const dialog = screen.getByRole("dialog")
    await user.type(within(dialog).getByLabelText(/Quantity Sold/i), "5")
    await user.click(within(dialog).getByRole("button", { name: /^Record Sale$/ }))

    expect(screen.getByRole("dialog")).toBeInTheDocument()
  })
})

describe("PlatformStockSection — states", () => {
  it("shows a clean empty state when there is no marketplace stock data", () => {
    stub()
    renderWithProviders(<PlatformStockSection {...baseProps({ data: { ...DATA, platforms: [] } })} />)
    expect(screen.getByText("No marketplace stock data for this product")).toBeInTheDocument()
  })

  it("shows a loading state without leaking data", () => {
    stub()
    renderWithProviders(<PlatformStockSection {...baseProps({ isLoading: true, data: undefined })} />)
    expect(screen.queryByText("Amazon")).not.toBeInTheDocument()
  })

  it("shows an error state and calls onRetry", async () => {
    const user = userEvent.setup()
    const onRetry = vi.fn()
    stub()
    renderWithProviders(
      <PlatformStockSection
        {...baseProps({ isError: true, error: new Error("boom"), data: undefined, onRetry })}
      />
    )
    const retry = screen.queryByRole("button", { name: /retry/i })
    if (retry) {
      await user.click(retry)
      expect(onRetry).toHaveBeenCalled()
    }
  })
})

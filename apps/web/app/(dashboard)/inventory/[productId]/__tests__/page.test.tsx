import { describe, expect, it, vi, beforeEach } from "vitest"
import { screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { renderWithProviders } from "@/test-utils/render-with-providers"
import InventoryProductPage, {
  EditNameDialog,
} from "@/app/(dashboard)/inventory/[productId]/page"
import {
  useAdjustProductStock,
  useAdjustVariantStock,
  useInventoryMovements,
  useInventoryProductStock,
  useSetProductName,
  useSetVariantName,
  useUpdatePacketsPerBox,
} from "@/services/inventory"
import { useAuth } from "@/lib/auth-context"
import type { InventoryProductStock } from "@/types/inventory"

const toastSuccess = vi.fn()
const toastError = vi.fn()
vi.mock("sonner", () => ({
  toast: { success: (m: string) => toastSuccess(m), error: (m: string) => toastError(m) },
}))

vi.mock("next/navigation", () => ({
  useParams: () => ({ productId: "prod-1" }),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn() }),
  usePathname: () => "/inventory/prod-1",
  useSearchParams: () => new URLSearchParams(),
}))

vi.mock("@/services/inventory", () => ({
  useInventoryProductStock: vi.fn(),
  useInventoryMovements: vi.fn(),
  useSetProductName: vi.fn(),
  useSetVariantName: vi.fn(),
  useUpdatePacketsPerBox: vi.fn(),
  useAdjustProductStock: vi.fn(),
  useAdjustVariantStock: vi.fn(),
}))

vi.mock("@/lib/auth-context", () => ({ useAuth: vi.fn() }))

const mockedUseProductStock = vi.mocked(useInventoryProductStock)
const mockedUseMovements = vi.mocked(useInventoryMovements)
const mockedUseSetProductName = vi.mocked(useSetProductName)
const mockedUseSetVariantName = vi.mocked(useSetVariantName)
const mockedUseUpdatePacketsPerBox = vi.mocked(useUpdatePacketsPerBox)
const mockedUseAdjustProductStock = vi.mocked(useAdjustProductStock)
const mockedUseAdjustVariantStock = vi.mocked(useAdjustVariantStock)
const mockedUseAuth = vi.mocked(useAuth)

/** `useMutation`-shaped stub: `mutate(value, { onSuccess, onError })` for
 * the name/packets dialogs, `mutateAsync(input)` for the stock dialogs.
 */
function mutationStub(behaviour: "success" | "error" = "success") {
  const mutate = vi.fn(
    (
      value: unknown,
      opts?: { onSuccess?: (d: unknown) => void; onError?: (e: unknown) => void }
    ) => {
      if (behaviour === "success")
        opts?.onSuccess?.({ display_title: value ?? "Shopify" })
      else opts?.onError?.(new Error("Server said no"))
    }
  )
  const mutateAsync = vi.fn(async () => {
    if (behaviour === "error") throw new Error("Server said no")
    return { id: "mv-1" }
  })
  return { mutate, mutateAsync, isPending: false } as unknown as ReturnType<
    typeof useSetProductName
  >
}

const SINGLE_VARIANT: InventoryProductStock = {
  product_id: "prod-1",
  product_name: "Vajrashakti",
  title: "Vajrashakti",
  title_override: null,
  available_boxes: 993,
  total_packets: 993,
  stock_status: "in_stock",
  variant_count: 1,
  packets_per_box_uniform: true,
  variant_ids: ["v-1"],
  variants: [
    {
      id: "v-1",
      sku: "VJR-SKT-30",
      variant_title: "Pack of 1",
      variant_title_override: null,
      display_title: "Pack of 1",
      available_boxes: 993,
      packets_per_box: 1,
      total_packets: 993,
      stock_status: "in_stock",
    },
  ],
}

const MULTI_VARIANT: InventoryProductStock = {
  product_id: "prod-1",
  product_name: "Aayush Wellness Herbal Masala",
  title: "आयुष हर्बल मसाला",
  title_override: "Aayush Wellness Herbal Masala",
  available_boxes: 6000,
  total_packets: 6000,
  stock_status: "in_stock",
  variant_count: 3,
  packets_per_box_uniform: true,
  variant_ids: ["v-1", "v-2", "v-3"],
  variants: [
    {
      id: "v-1",
      sku: "AW-HM-PN-60",
      variant_title: "पान मसाला स्वाद",
      variant_title_override: "Paan Masala Flavour",
      display_title: "Paan Masala Flavour",
      available_boxes: 2000,
      packets_per_box: 1,
      total_packets: 2000,
      stock_status: "in_stock",
    },
    {
      id: "v-2",
      sku: "AW-HM-CR-60",
      variant_title: "गुटका स्वाद",
      variant_title_override: "Gutka Flavour",
      display_title: "Gutka Flavour",
      available_boxes: 2000,
      packets_per_box: 1,
      total_packets: 2000,
      stock_status: "in_stock",
    },
    {
      id: "v-3",
      sku: "AW-HM-RG-60",
      variant_title: "रॉयल तंबाकू स्वाद",
      variant_title_override: "Royal Tobacco Flavour",
      display_title: "Royal Tobacco Flavour",
      available_boxes: 2000,
      packets_per_box: 1,
      total_packets: 2000,
      stock_status: "in_stock",
    },
  ],
}

function setProduct(data: InventoryProductStock) {
  mockedUseProductStock.mockReturnValue({
    data,
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  } as unknown as ReturnType<typeof useInventoryProductStock>)
}

beforeEach(() => {
  toastSuccess.mockClear()
  toastError.mockClear()
  mockedUseSetProductName.mockReturnValue(mutationStub())
  mockedUseSetVariantName.mockReturnValue(mutationStub())
  mockedUseUpdatePacketsPerBox.mockReturnValue(mutationStub() as never)
  mockedUseAdjustProductStock.mockReturnValue(mutationStub() as never)
  mockedUseAdjustVariantStock.mockReturnValue(mutationStub() as never)
  mockedUseAuth.mockReturnValue({
    hasPermission: () => true,
  } as unknown as ReturnType<typeof useAuth>)
  mockedUseMovements.mockReturnValue({
    data: { data: [], meta: { page: 1, page_size: 20, total_items: 0, total_pages: 0 } },
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  } as unknown as ReturnType<typeof useInventoryMovements>)
  setProduct(MULTI_VARIANT)
})

describe("InventoryProductPage — one card per product", () => {
  it("renders exactly ONE product inventory card with aggregated totals", () => {
    renderWithProviders(<InventoryProductPage />)

    expect(
      screen.getByRole("heading", { name: "Aayush Wellness Herbal Masala" })
    ).toBeInTheDocument()
    // aggregated, not per-variant
    expect(screen.getByText("6,000 boxes")).toBeInTheDocument()
    expect(screen.getByText("3 variants · 1 packets/box")).toBeInTheDocument()
    // one Edit Stock button = one card
    expect(screen.getAllByRole("button", { name: "Edit Stock" })).toHaveLength(1)
  })

  it("does NOT render Pack of 1 / Pack of 2 / Pack of 3 (or flavours) as separate cards", () => {
    setProduct(SINGLE_VARIANT)
    renderWithProviders(<InventoryProductPage />)

    // "Pack of 1" is the sole variant; it must not be a heading/card of its own
    expect(screen.queryByRole("heading", { name: "Pack of 1" })).not.toBeInTheDocument()
    expect(screen.getByRole("heading", { name: "Vajrashakti" })).toBeInTheDocument()
    expect(screen.getAllByRole("heading", { level: 2 }).length).toBeLessThanOrEqual(1) // only "Movement History"
  })

  it("flavour variants live only inside the collapsible Underlying variants list", async () => {
    const user = userEvent.setup()
    renderWithProviders(<InventoryProductPage />)

    // not a heading anywhere
    expect(
      screen.queryByRole("heading", { name: "Gutka Flavour" })
    ).not.toBeInTheDocument()

    await user.click(screen.getByText(/Underlying variants \(3\)/))
    expect(screen.getByText("Paan Masala Flavour")).toBeInTheDocument()
    expect(screen.getByText("Gutka Flavour")).toBeInTheDocument()
    expect(screen.getByText("Royal Tobacco Flavour")).toBeInTheDocument()
  })

  it("shows 'mixed pack sizes' when variants disagree on packets_per_box", () => {
    setProduct({
      ...MULTI_VARIANT,
      packets_per_box_uniform: false,
      variants: MULTI_VARIANT.variants.map((v, i) => ({ ...v, packets_per_box: i + 1 })),
    })
    renderWithProviders(<InventoryProductPage />)
    expect(screen.getByText("3 variants · mixed pack sizes")).toBeInTheDocument()
  })

  it("uses the English display name (title_override), never the Hindi source title", () => {
    renderWithProviders(<InventoryProductPage />)
    expect(
      screen.getByRole("heading", { name: "Aayush Wellness Herbal Masala" })
    ).toBeInTheDocument()
    expect(screen.queryByText("आयुष हर्बल मसाला")).not.toBeInTheDocument()
  })
})

describe("InventoryProductPage — Edit Stock", () => {
  it("single-variant product: one field, saves via the product-level endpoint", async () => {
    const user = userEvent.setup()
    const adjust = mutationStub("success")
    mockedUseAdjustProductStock.mockReturnValue(adjust as never)
    setProduct(SINGLE_VARIANT)
    renderWithProviders(<InventoryProductPage />)

    await user.click(screen.getByRole("button", { name: "Edit Stock" }))
    const dialog = screen.getByRole("dialog")
    const input = within(dialog).getByLabelText("New")
    await user.clear(input)
    await user.type(input, "1000")
    await user.type(within(dialog).getByLabelText("Reason"), "New stock received")
    await user.click(within(dialog).getByRole("button", { name: "Save" }))

    expect(adjust.mutateAsync).toHaveBeenCalledWith({
      target_boxes: 1000,
      reason: "New stock received",
    })
    expect(toastSuccess).toHaveBeenCalled()
  })

  it("multi-variant product: one row per variant, one call per changed row", async () => {
    const user = userEvent.setup()
    const adjustVariant = mutationStub("success")
    mockedUseAdjustVariantStock.mockReturnValue(adjustVariant as never)
    renderWithProviders(<InventoryProductPage />)

    await user.click(screen.getByRole("button", { name: "Edit Stock" }))
    const dialog = screen.getByRole("dialog")
    const inputs = within(dialog).getAllByLabelText("New")
    expect(inputs).toHaveLength(3) // one row per underlying variant

    await user.clear(inputs[0])
    await user.type(inputs[0], "2100")
    await user.clear(inputs[2])
    await user.type(inputs[2], "1900")
    await user.type(within(dialog).getByLabelText("Reason"), "Stocktake correction")
    await user.click(within(dialog).getByRole("button", { name: "Save" }))

    expect(adjustVariant.mutateAsync).toHaveBeenCalledTimes(2)
    expect(adjustVariant.mutateAsync).toHaveBeenCalledWith({
      variantId: "v-1",
      target_boxes: 2100,
      reason: "Stocktake correction",
    })
    expect(adjustVariant.mutateAsync).toHaveBeenCalledWith({
      variantId: "v-3",
      target_boxes: 1900,
      reason: "Stocktake correction",
    })
  })

  it("Save is disabled without a reason or without any changed row", async () => {
    const user = userEvent.setup()
    renderWithProviders(<InventoryProductPage />)
    await user.click(screen.getByRole("button", { name: "Edit Stock" }))
    const dialog = screen.getByRole("dialog")

    expect(within(dialog).getByRole("button", { name: "Save" })).toBeDisabled() // nothing changed
    const inputs = within(dialog).getAllByLabelText("New")
    await user.clear(inputs[0])
    await user.type(inputs[0], "2100")
    expect(within(dialog).getByRole("button", { name: "Save" })).toBeDisabled() // still no reason
    await user.type(within(dialog).getByLabelText("Reason"), "x")
    expect(within(dialog).getByRole("button", { name: "Save" })).toBeEnabled()
  })

  it("rejects a negative target at the input (min=0) and shows the delta preview", async () => {
    const user = userEvent.setup()
    renderWithProviders(<InventoryProductPage />)
    await user.click(screen.getByRole("button", { name: "Edit Stock" }))
    const dialog = screen.getByRole("dialog")
    const inputs = within(dialog).getAllByLabelText("New")
    expect(inputs[0]).toHaveAttribute("min", "0")

    await user.clear(inputs[0])
    await user.type(inputs[0], "2500")
    expect(within(dialog).getByText("+500 boxes")).toBeInTheDocument()
  })

  it("keeps the dialog open and shows the API error when a save fails", async () => {
    const user = userEvent.setup()
    mockedUseAdjustVariantStock.mockReturnValue(mutationStub("error") as never)
    renderWithProviders(<InventoryProductPage />)

    await user.click(screen.getByRole("button", { name: "Edit Stock" }))
    const dialog = screen.getByRole("dialog")
    const inputs = within(dialog).getAllByLabelText("New")
    await user.clear(inputs[0])
    await user.type(inputs[0], "2100")
    await user.type(within(dialog).getByLabelText("Reason"), "oops")
    await user.click(within(dialog).getByRole("button", { name: "Save" }))

    expect(toastError).toHaveBeenCalledWith("Server said no")
    expect(screen.getByRole("dialog")).toBeInTheDocument()
  })
})

describe("InventoryProductPage — Edit Name & History", () => {
  it("shows Edit Stock / Edit Name for inventory.manage and hides them for read-only", () => {
    const { unmount } = renderWithProviders(<InventoryProductPage />)
    expect(
      screen.getAllByRole("button", { name: "Edit Name" }).length
    ).toBeGreaterThanOrEqual(1)
    expect(screen.getByRole("button", { name: "Edit Stock" })).toBeInTheDocument()
    unmount()

    mockedUseAuth.mockReturnValue({
      hasPermission: (code: string) => code !== "inventory.manage",
    } as unknown as ReturnType<typeof useAuth>)
    renderWithProviders(<InventoryProductPage />)
    expect(screen.queryByRole("button", { name: "Edit Stock" })).not.toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "Edit Name" })).not.toBeInTheDocument()
  })

  it("renders the product-level Movement History and toggles it", async () => {
    const user = userEvent.setup()
    renderWithProviders(<InventoryProductPage />)

    expect(
      screen.getByRole("heading", {
        name: "Movement History — Aayush Wellness Herbal Masala",
      })
    ).toBeInTheDocument()

    await user.click(screen.getByRole("button", { name: "Hide History" }))
    expect(
      screen.queryByRole("heading", { name: /Movement History/ })
    ).not.toBeInTheDocument()
  })
})

describe("EditNameDialog", () => {
  it("opens, pre-fills, saves a trimmed name, closes on success", async () => {
    const user = userEvent.setup()
    const mutation = mutationStub("success")
    renderWithProviders(
      <EditNameDialog
        kind="product"
        currentDisplayName="Current Name"
        shopifyName="Shopify Name"
        hasOverride={false}
        maxLength={500}
        mutation={mutation}
      />
    )

    await user.click(screen.getByRole("button", { name: "Edit Name" }))
    const dialog = screen.getByRole("dialog")
    expect(within(dialog).getByText("Edit Product Name")).toBeInTheDocument()
    expect(within(dialog).getByText("Shopify Name")).toBeInTheDocument()
    const input = within(dialog).getByLabelText("Display name")
    expect(input).toHaveValue("Current Name")
    await user.clear(input)
    await user.type(input, "  New Name  ")
    await user.click(within(dialog).getByRole("button", { name: "Save" }))

    expect(mutation.mutate).toHaveBeenCalledWith("New Name", expect.anything())
    expect(toastSuccess).toHaveBeenCalledWith("Product name updated.")
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument()
  })

  it("disables Save for empty/whitespace and shows the error", async () => {
    const user = userEvent.setup()
    const mutation = mutationStub()
    renderWithProviders(
      <EditNameDialog
        kind="variant"
        currentDisplayName="Something"
        shopifyName={null}
        hasOverride={false}
        maxLength={255}
        mutation={mutation}
      />
    )
    await user.click(screen.getByRole("button", { name: "Edit Name" }))
    const input = screen.getByLabelText("Display name")
    await user.clear(input)
    await user.type(input, "   ")
    expect(screen.getByText("Name cannot be empty.")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled()
    await user.click(screen.getByRole("button", { name: "Save" }))
    expect(mutation.mutate).not.toHaveBeenCalled()
  })

  it("keeps the dialog open and surfaces the API error on failure", async () => {
    const user = userEvent.setup()
    const mutation = mutationStub("error")
    renderWithProviders(
      <EditNameDialog
        kind="product"
        currentDisplayName="Old"
        shopifyName="Old"
        hasOverride={false}
        maxLength={500}
        mutation={mutation}
      />
    )
    await user.click(screen.getByRole("button", { name: "Edit Name" }))
    await user.clear(screen.getByLabelText("Display name"))
    await user.type(screen.getByLabelText("Display name"), "New")
    await user.click(screen.getByRole("button", { name: "Save" }))
    expect(toastError).toHaveBeenCalledWith("Server said no")
    expect(screen.getByRole("dialog")).toBeInTheDocument()
  })

  it("Cancel closes without calling the mutation", async () => {
    const user = userEvent.setup()
    const mutation = mutationStub()
    renderWithProviders(
      <EditNameDialog
        kind="product"
        currentDisplayName="Old"
        shopifyName="Old"
        hasOverride={false}
        maxLength={500}
        mutation={mutation}
      />
    )
    await user.click(screen.getByRole("button", { name: "Edit Name" }))
    await user.click(screen.getByRole("button", { name: "Cancel" }))
    expect(mutation.mutate).not.toHaveBeenCalled()
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument()
  })

  it("Reset to Shopify Name shows only with an override and clears it", async () => {
    const user = userEvent.setup()
    const mutation = mutationStub("success")
    const { rerender } = renderWithProviders(
      <EditNameDialog
        kind="product"
        currentDisplayName="Custom"
        shopifyName="Shopify Name"
        hasOverride={false}
        maxLength={500}
        mutation={mutation}
      />
    )
    await user.click(screen.getByRole("button", { name: "Edit Name" }))
    expect(
      screen.queryByRole("button", { name: "Reset to Shopify Name" })
    ).not.toBeInTheDocument()
    await user.click(screen.getByRole("button", { name: "Cancel" }))

    rerender(
      <EditNameDialog
        kind="product"
        currentDisplayName="Custom"
        shopifyName="Shopify Name"
        hasOverride
        maxLength={500}
        mutation={mutation}
      />
    )
    await user.click(screen.getByRole("button", { name: "Edit Name" }))
    await user.click(screen.getByRole("button", { name: "Reset to Shopify Name" }))
    expect(mutation.mutate).toHaveBeenCalledWith(null, expect.anything())
    expect(toastSuccess).toHaveBeenCalledWith("Reset to Shopify name.")
  })
})

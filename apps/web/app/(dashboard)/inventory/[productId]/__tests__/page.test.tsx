import { describe, expect, it, vi, beforeEach } from "vitest"
import { fireEvent, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { renderWithProviders } from "@/test-utils/render-with-providers"
import InventoryProductPage, {
  EditNameDialog,
} from "@/app/(dashboard)/inventory/[productId]/page"
import {
  useAdjustVariantStock,
  useInventoryMovements,
  useInventoryProductStock,
  useSetCatalogVariantName,
  useSetProductName,
  useSetVariantName,
  useUpdatePacketsPerBox,
} from "@/services/inventory"
import { useAuth } from "@/lib/auth-context"
import type {
  InventoryMovement,
  InventoryProductStock,
  OmsCatalogVariant,
  ProductVariantStockLine,
} from "@/types/inventory"

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
  useSetCatalogVariantName: vi.fn(),
  useUpdatePacketsPerBox: vi.fn(),
  useAdjustVariantStock: vi.fn(),
}))

vi.mock("@/lib/auth-context", () => ({ useAuth: vi.fn() }))

const mockedUseProductStock = vi.mocked(useInventoryProductStock)
const mockedUseMovements = vi.mocked(useInventoryMovements)
const mockedUseSetProductName = vi.mocked(useSetProductName)
const mockedUseSetVariantName = vi.mocked(useSetVariantName)
const mockedUseSetCatalogVariantName = vi.mocked(useSetCatalogVariantName)
const mockedUseUpdatePacketsPerBox = vi.mocked(useUpdatePacketsPerBox)
const mockedUseAdjustVariantStock = vi.mocked(useAdjustVariantStock)
const mockedUseAuth = vi.mocked(useAuth)

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

function line(
  over: Partial<ProductVariantStockLine> & { id: string; sku: string }
): ProductVariantStockLine {
  return {
    variant_title: over.sku,
    variant_title_override: null,
    display_title: over.sku,
    available_boxes: 100,
    packets_per_box: 1,
    total_packets: 100,
    stock_status: "in_stock",
    image_url: null,
    ...over,
  }
}

function omsVariant(
  over: Partial<OmsCatalogVariant> & { name: string }
): OmsCatalogVariant {
  const underlying = over.underlying_variants ?? []
  return {
    catalog_variant_id: "cv-1",
    display_order: 0,
    is_active: true,
    available_boxes: underlying.reduce((s, u) => s + u.available_boxes, 0),
    total_packets: underlying.reduce(
      (s, u) => s + u.available_boxes * u.packets_per_box,
      0
    ),
    stock_status: "in_stock",
    packets_per_box_uniform: new Set(underlying.map((u) => u.packets_per_box)).size <= 1,
    underlying_variant_count: underlying.length,
    image_url: null,
    ...over,
    underlying_variants: underlying,
  }
}

// Aayush Herbal Masala: 3 OMS flavour variants, each grouping a 60- and a 120-pack SKU.
const HERBAL_MASALA: InventoryProductStock = {
  product_id: "prod-1",
  product_name: "Aayush Wellness Herbal Masala",
  title: "आयुष हर्बल मसाला",
  title_override: "Aayush Wellness Herbal Masala",
  image_url: "https://cdn.shopify.com/s/files/1/herbal-masala.jpg",
  available_boxes: 630,
  total_packets: 630,
  stock_status: "in_stock",
  packets_per_box_uniform: false,
  oms_variant_count: 3,
  underlying_variant_count: 6,
  oms_variants: [
    omsVariant({
      catalog_variant_id: "cv-royal",
      name: "Royal Tobacco Flavour",
      display_order: 0,
      image_url: "https://cdn.shopify.com/s/files/1/royal-tobacco.jpg",
      underlying_variants: [
        line({
          id: "v-rg60",
          sku: "AW-HM-RG-60",
          available_boxes: 10,
          packets_per_box: 60,
        }),
        line({
          id: "v-rg120",
          sku: "AW-HM-RG-120",
          available_boxes: 3,
          packets_per_box: 120,
        }),
      ],
    }),
    omsVariant({
      catalog_variant_id: "cv-gutka",
      name: "Ghutka Flavour",
      display_order: 1,
      // No distinct variant image -- the API already resolved this to
      // the product's featured image server-side (see backend
      // `_resolve_oms_variant_image`); the frontend just renders it.
      image_url: "https://cdn.shopify.com/s/files/1/herbal-masala.jpg",
      underlying_variants: [
        line({
          id: "v-gu60",
          sku: "AW-HM-CR-60",
          available_boxes: 100,
          packets_per_box: 60,
        }),
        line({
          id: "v-gu120",
          sku: "AW-HM-CR-120",
          available_boxes: 40,
          packets_per_box: 120,
        }),
      ],
    }),
    omsVariant({
      catalog_variant_id: "cv-paan",
      name: "Paan Masala Flavour",
      display_order: 2,
      underlying_variants: [
        line({
          id: "v-pn60",
          sku: "AW-HM-PN-60",
          available_boxes: 200,
          packets_per_box: 60,
        }),
        line({
          id: "v-pn120",
          sku: "AW-HM-PN-120",
          available_boxes: 55,
          packets_per_box: 120,
        }),
      ],
    }),
  ],
}

// A non-Herbal-Masala product: ONE OMS variant grouping 3 pack SKUs.
const VAJRASHAKTI: InventoryProductStock = {
  product_id: "prod-1",
  product_name: "Vajrashakti",
  title: "Vajrashakti",
  title_override: null,
  image_url: null,
  available_boxes: 900,
  total_packets: 900,
  stock_status: "in_stock",
  packets_per_box_uniform: true,
  oms_variant_count: 1,
  underlying_variant_count: 3,
  oms_variants: [
    omsVariant({
      catalog_variant_id: "cv-vjr",
      name: "Vajrashakti",
      underlying_variants: [
        line({
          id: "v-1",
          sku: "VJR-30",
          display_title: "Pack of 1",
          available_boxes: 300,
        }),
        line({
          id: "v-2",
          sku: "VJR-60",
          display_title: "Pack of 2",
          available_boxes: 400,
        }),
        line({
          id: "v-3",
          sku: "VJR-90",
          display_title: "Pack of 3",
          available_boxes: 200,
        }),
      ],
    }),
  ],
}

// An implicit (ungrouped) OMS variant -- catalog_variant_id null, 1 underlying row.
const IMPLICIT: InventoryProductStock = {
  product_id: "prod-1",
  product_name: "Ungrouped Product",
  title: "Ungrouped Product",
  title_override: null,
  image_url: null,
  available_boxes: 5,
  total_packets: 5,
  stock_status: "low_stock",
  packets_per_box_uniform: true,
  oms_variant_count: 1,
  underlying_variant_count: 1,
  oms_variants: [
    omsVariant({
      catalog_variant_id: null,
      name: "Default Title",
      underlying_variants: [line({ id: "v-only", sku: "SKU-ONLY", available_boxes: 5 })],
    }),
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
  mockedUseSetCatalogVariantName.mockReturnValue(mutationStub() as never)
  mockedUseUpdatePacketsPerBox.mockReturnValue(mutationStub() as never)
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
  setProduct(HERBAL_MASALA)
})

describe("InventoryProductPage — OMS-visible variants only", () => {
  it("Aayush Herbal Masala shows exactly 3 OMS-visible variant cards (the flavours)", () => {
    renderWithProviders(<InventoryProductPage />)

    for (const name of [
      "Royal Tobacco Flavour",
      "Ghutka Flavour",
      "Paan Masala Flavour",
    ]) {
      expect(screen.getByText(name)).toBeInTheDocument()
    }
    expect(screen.getAllByRole("button", { name: "Edit Stock" })).toHaveLength(3)
  })

  it("does NOT show Pack-size / 60-120 pouch SKUs as OMS-visible variant headings", () => {
    renderWithProviders(<InventoryProductPage />)
    expect(
      screen.queryByRole("heading", { name: /60 - Pouches|AW-HM-CR-60|Pack of 1/ })
    ).toBeNull()
    expect(screen.queryByRole("heading", { name: /Pack of 2|Pack of 3/ })).toBeNull()
  })

  it("every OMS-visible card shows its SKU(s) directly, with no click needed", () => {
    renderWithProviders(<InventoryProductPage />)
    // Ghutka Flavour groups AW-HM-CR-60 + AW-HM-CR-120 -- both listed on
    // the card face, comma-separated, before any "Underlying Shopify
    // variants" section is expanded.
    const skuFields = screen.getAllByTestId("oms-variant-skus")
    expect(skuFields.map((el) => el.textContent)).toEqual(
      expect.arrayContaining([
        "AW-HM-RG-60, AW-HM-RG-120",
        "AW-HM-CR-60, AW-HM-CR-120",
        "AW-HM-PN-60, AW-HM-PN-120",
      ])
    )
  })

  it("underlying Shopify SKU detail also appears inside the collapsible section", async () => {
    const user = userEvent.setup()
    renderWithProviders(<InventoryProductPage />)

    // collapsed by default: the detail text is present in the DOM but not as a heading
    expect(screen.queryByRole("heading", { name: /AW-HM-CR-60/ })).toBeNull()
    const summaries = screen.getAllByText(/Underlying Shopify variants \(2\)/)
    expect(summaries).toHaveLength(3)
    await user.click(summaries[1]) // Ghutka
    expect(screen.getByText(/SKU AW-HM-CR-60/)).toBeInTheDocument()
    expect(screen.getByText(/SKU AW-HM-CR-120/)).toBeInTheDocument()
  })

  it("a non-Herbal-Masala product shows exactly ONE OMS-visible variant", () => {
    setProduct(VAJRASHAKTI)
    renderWithProviders(<InventoryProductPage />)
    expect(screen.getAllByRole("button", { name: "Edit Stock" })).toHaveLength(1)
    expect(screen.getByRole("heading", { name: "Vajrashakti" })).toBeInTheDocument()
    // its 3 pack-size SKUs are all grouped into that one card and all
    // shown directly on the card face
    expect(screen.getByTestId("oms-variant-skus").textContent).toBe(
      "VJR-30, VJR-60, VJR-90"
    )
  })

  it("shows aggregated boxes/packets per OMS variant and 'mixed pack sizes'", () => {
    renderWithProviders(<InventoryProductPage />)
    // Ghutka = 100 + 40 boxes
    expect(screen.getByText("140 boxes")).toBeInTheDocument()
    // each flavour groups a 60- and a 120-pack -> mixed
    expect(screen.getAllByText(/mixed pack sizes/).length).toBeGreaterThanOrEqual(3)
  })

  it("uses the English OMS display name, never the Hindi source title", () => {
    renderWithProviders(<InventoryProductPage />)
    expect(screen.getByText("Ghutka Flavour")).toBeInTheDocument()
    expect(screen.queryByText("आयुष हर्बल मसाला")).not.toBeInTheDocument()
  })
})

describe("InventoryProductPage — CatalogVariant images", () => {
  it("renders an OMS variant's resolved image", () => {
    const { container } = renderWithProviders(<InventoryProductPage />)
    const img = container.querySelector('img[alt="Royal Tobacco Flavour"]')
    expect(img).toHaveAttribute("src", "https://cdn.shopify.com/s/files/1/royal-tobacco.jpg")
  })

  it("falls back to the product image when a group has no distinct variant image", () => {
    const { container } = renderWithProviders(<InventoryProductPage />)
    // Ghutka Flavour has no image_url of its own -- the API already
    // resolved this to the product's featured image server-side.
    const img = container.querySelector('img[alt="Ghutka Flavour"]')
    expect(img).toHaveAttribute("src", "https://cdn.shopify.com/s/files/1/herbal-masala.jpg")
  })

  it("renders a neutral placeholder, never a broken <img>, when image_url is null", () => {
    setProduct(VAJRASHAKTI)
    const { container } = renderWithProviders(<InventoryProductPage />)
    // VAJRASHAKTI's OMS variant and product image are both null.
    expect(container.querySelector('img[alt="Vajrashakti"]')).toBeNull()
    expect(screen.getByRole("img", { name: "Vajrashakti" })).toBeInTheDocument()
  })

  it("falls back to the placeholder when the image URL fails to load", () => {
    const { container } = renderWithProviders(<InventoryProductPage />)
    const img = container.querySelector('img[alt="Royal Tobacco Flavour"]') as HTMLImageElement
    expect(img).not.toBeNull()
    fireEvent.error(img)
    // After the load error, the <img> is replaced by the placeholder --
    // no broken-image icon, still findable by the same accessible name.
    expect(container.querySelector('img[alt="Royal Tobacco Flavour"]')).toBeNull()
    expect(screen.getByRole("img", { name: "Royal Tobacco Flavour" })).toBeInTheDocument()
  })
})

describe("InventoryProductPage — Edit Stock (per underlying SKU, never distributed)", () => {
  it("a grouped OMS variant gets one row per underlying SKU; one call per changed row", async () => {
    const user = userEvent.setup()
    const adjust = mutationStub("success")
    mockedUseAdjustVariantStock.mockReturnValue(adjust as never)
    renderWithProviders(<InventoryProductPage />)

    // open Ghutka's Edit Stock (2nd card)
    await user.click(screen.getAllByRole("button", { name: "Edit Stock" })[1])
    const dialog = screen.getByRole("dialog")
    expect(within(dialog).getByText("Edit Stock — Ghutka Flavour")).toBeInTheDocument()
    const inputs = within(dialog).getAllByLabelText("New")
    expect(inputs).toHaveLength(2) // one row per underlying Shopify SKU

    await user.clear(inputs[0])
    await user.type(inputs[0], "150")
    await user.type(within(dialog).getByLabelText("Reason"), "stocktake")
    await user.click(within(dialog).getByRole("button", { name: "Save" }))

    expect(adjust.mutateAsync).toHaveBeenCalledTimes(1)
    expect(adjust.mutateAsync).toHaveBeenCalledWith({
      variantId: "v-gu60",
      target_boxes: 150,
      reason: "stocktake",
    })
  })

  it("a single-underlying OMS variant gets one field and still edits that SKU directly", async () => {
    const user = userEvent.setup()
    const adjust = mutationStub("success")
    mockedUseAdjustVariantStock.mockReturnValue(adjust as never)
    setProduct(IMPLICIT)
    renderWithProviders(<InventoryProductPage />)

    await user.click(screen.getByRole("button", { name: "Edit Stock" }))
    const dialog = screen.getByRole("dialog")
    const inputs = within(dialog).getAllByLabelText("New")
    expect(inputs).toHaveLength(1)
    await user.clear(inputs[0])
    await user.type(inputs[0], "9")
    await user.type(within(dialog).getByLabelText("Reason"), "count")
    await user.click(within(dialog).getByRole("button", { name: "Save" }))

    expect(adjust.mutateAsync).toHaveBeenCalledWith({
      variantId: "v-only",
      target_boxes: 9,
      reason: "count",
    })
  })

  it("Save is disabled without a reason or a changed row; min=0 on inputs; delta preview", async () => {
    const user = userEvent.setup()
    renderWithProviders(<InventoryProductPage />)
    await user.click(screen.getAllByRole("button", { name: "Edit Stock" })[1])
    const dialog = screen.getByRole("dialog")
    expect(within(dialog).getByRole("button", { name: "Save" })).toBeDisabled()
    const inputs = within(dialog).getAllByLabelText("New")
    expect(inputs[0]).toHaveAttribute("min", "0")
    await user.clear(inputs[0])
    await user.type(inputs[0], "150")
    expect(within(dialog).getByText("+50 boxes")).toBeInTheDocument()
    expect(within(dialog).getByRole("button", { name: "Save" })).toBeDisabled() // still no reason
    await user.type(within(dialog).getByLabelText("Reason"), "x")
    expect(within(dialog).getByRole("button", { name: "Save" })).toBeEnabled()
  })

  it("keeps the dialog open and shows the API error on a failed save", async () => {
    const user = userEvent.setup()
    mockedUseAdjustVariantStock.mockReturnValue(mutationStub("error") as never)
    renderWithProviders(<InventoryProductPage />)
    await user.click(screen.getAllByRole("button", { name: "Edit Stock" })[1])
    const dialog = screen.getByRole("dialog")
    const inputs = within(dialog).getAllByLabelText("New")
    await user.clear(inputs[0])
    await user.type(inputs[0], "150")
    await user.type(within(dialog).getByLabelText("Reason"), "oops")
    await user.click(within(dialog).getByRole("button", { name: "Save" }))
    expect(toastError).toHaveBeenCalledWith("Server said no")
    expect(screen.getByRole("dialog")).toBeInTheDocument()
  })
})

describe("InventoryProductPage — Edit Name & History", () => {
  it("Edit Name on a grouped OMS variant renames the CatalogVariant", async () => {
    const user = userEvent.setup()
    const rename = mutationStub("success")
    mockedUseSetCatalogVariantName.mockReturnValue(rename as never)
    renderWithProviders(<InventoryProductPage />)

    // scope to Ghutka's own action row -- its "Underlying Shopify variants"
    // rows also render "Edit Name" buttons, so an unscoped query is ambiguous.
    const ghutkaActions = screen.getByTestId("oms-variant-actions-cv-gutka")
    await user.click(within(ghutkaActions).getByRole("button", { name: "Edit Name" }))
    const dialog = screen.getByRole("dialog")
    const input = within(dialog).getByLabelText("Display name")
    await user.clear(input)
    await user.type(input, "  Gutka Flavour  ")
    await user.click(within(dialog).getByRole("button", { name: "Save" }))

    expect(rename.mutate).toHaveBeenCalledWith("Gutka Flavour", expect.anything())
  })

  it("Edit Name on an implicit OMS variant edits the underlying variant title override", async () => {
    const user = userEvent.setup()
    const setVariant = mutationStub("success")
    mockedUseSetVariantName.mockReturnValue(setVariant)
    setProduct(IMPLICIT)
    renderWithProviders(<InventoryProductPage />)

    const actions = screen.getByTestId("oms-variant-actions-v-only")
    await user.click(within(actions).getByRole("button", { name: "Edit Name" }))
    const dialog = screen.getByRole("dialog")
    const input = within(dialog).getByLabelText("Display name")
    await user.clear(input)
    await user.type(input, "Nice Name")
    await user.click(within(dialog).getByRole("button", { name: "Save" }))
    expect(setVariant.mutate).toHaveBeenCalledWith("Nice Name", expect.anything())
  })

  it("hides Edit Stock / Edit Name for a read-only user", () => {
    mockedUseAuth.mockReturnValue({
      hasPermission: (code: string) => code !== "inventory.manage",
    } as unknown as ReturnType<typeof useAuth>)
    renderWithProviders(<InventoryProductPage />)
    expect(screen.queryByRole("button", { name: "Edit Stock" })).not.toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "Edit Name" })).not.toBeInTheDocument()
    // still shows the 3 flavour cards read-only
    expect(screen.getByText("Ghutka Flavour")).toBeInTheDocument()
  })

  it("History per OMS variant toggles and notes it spans multiple Shopify SKUs", async () => {
    const user = userEvent.setup()
    renderWithProviders(<InventoryProductPage />)

    expect(screen.queryByText(/Movement History — Ghutka Flavour/)).toBeNull()
    await user.click(screen.getAllByRole("button", { name: "History" })[1])
    expect(screen.getByText(/Movement History — Ghutka Flavour/)).toBeInTheDocument()
    expect(screen.getByText(/spans 2 Shopify SKUs/)).toBeInTheDocument()
    await user.click(screen.getAllByRole("button", { name: "Hide History" })[0])
    expect(screen.queryByText(/Movement History — Ghutka Flavour/)).toBeNull()
  })
})

function movement(
  over: Partial<InventoryMovement> & { id: string; movement_type: InventoryMovement["movement_type"] }
): InventoryMovement {
  return {
    product_variant_id: "v-1",
    product_id: "prod-1",
    product_title: "Vajrashakti",
    variant_title: "Pack of 1",
    variant_display_title: "Pack of 1",
    catalog_variant_id: "cv-vjr",
    sku: "VJR-30",
    quantity_delta: -1,
    previous_balance: 300,
    quantity_after: 299,
    order_id: null,
    shipment_id: null,
    rto_id: null,
    actor_user_id: null,
    actor_label: "Shiprocket",
    reason: null,
    notes: null,
    created_at: "2026-01-01T00:00:00Z",
    ...over,
  }
}

function setMovements(rows: InventoryMovement[]) {
  mockedUseMovements.mockReturnValue({
    data: { data: rows, meta: { page: 1, page_size: 20, total_items: rows.length, total_pages: 1 } },
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  } as unknown as ReturnType<typeof useInventoryMovements>)
}

describe("InventoryProductPage — Movement History display", () => {
  beforeEach(() => {
    setProduct(VAJRASHAKTI) // single OMS-visible card -- exactly one "History" button
  })

  it("renders a dispatch as 'Shipped: -1 box'", async () => {
    const user = userEvent.setup()
    setMovements([
      movement({
        id: "m-1",
        movement_type: "dispatch",
        quantity_delta: -1,
        previous_balance: 300,
        quantity_after: 299,
      }),
    ])
    renderWithProviders(<InventoryProductPage />)
    await user.click(screen.getByRole("button", { name: "History" }))
    expect(screen.getByText("Shipped: -1 box")).toBeInTheDocument()
  })

  it("renders an RTO restock as 'RTO Delivered: +1 box'", async () => {
    const user = userEvent.setup()
    setMovements([
      movement({
        id: "m-2",
        movement_type: "rto_restock",
        quantity_delta: 1,
        previous_balance: 298,
        quantity_after: 299,
      }),
    ])
    renderWithProviders(<InventoryProductPage />)
    await user.click(screen.getByRole("button", { name: "History" }))
    expect(screen.getByText("RTO Delivered: +1 box")).toBeInTheDocument()
  })

  it("pluralizes to 'boxes' for any magnitude other than 1", async () => {
    const user = userEvent.setup()
    setMovements([
      movement({
        id: "m-3",
        movement_type: "manual_adjustment",
        quantity_delta: 5,
        previous_balance: 294,
        quantity_after: 299,
      }),
      movement({
        id: "m-4",
        movement_type: "initial_stock",
        quantity_delta: 10,
        previous_balance: 0,
        quantity_after: 10,
      }),
    ])
    renderWithProviders(<InventoryProductPage />)
    await user.click(screen.getByRole("button", { name: "History" }))
    expect(screen.getByText("Manual adjustment: +5 boxes")).toBeInTheDocument()
    expect(screen.getByText("Initial stock: +10 boxes")).toBeInTheDocument()
  })

  it("keeps SKU visible and New balance shown in boxes alongside the combined column", async () => {
    const user = userEvent.setup()
    setMovements([
      movement({
        id: "m-5",
        movement_type: "dispatch",
        sku: "VJR-30",
        quantity_delta: -1,
        previous_balance: 300,
        quantity_after: 299,
      }),
    ])
    renderWithProviders(<InventoryProductPage />)
    await user.click(screen.getByRole("button", { name: "History" }))
    expect(screen.getByText("Shipped: -1 box")).toBeInTheDocument()
    expect(screen.getByText("VJR-30")).toBeInTheDocument()
    expect(screen.getByText("299 boxes")).toBeInTheDocument() // New balance
    expect(screen.getByText("300 boxes")).toBeInTheDocument() // Previous balance, unchanged
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

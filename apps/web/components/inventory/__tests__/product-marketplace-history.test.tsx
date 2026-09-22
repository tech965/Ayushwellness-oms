import { beforeEach, describe, expect, it, vi } from "vitest"
import { screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { ProductMarketplaceHistory } from "@/components/inventory/product-marketplace-history"
import { renderWithProviders } from "@/test-utils/render-with-providers"
import {
  useEditMarketplaceMovement,
  useProductMarketplaceHistory,
  useUndoMarketplaceMovement,
} from "@/services/platform-inventory"
import type { ProductMarketplaceMovement } from "@/types/platform-inventory"

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

vi.mock("@/services/platform-inventory", () => ({
  useProductMarketplaceHistory: vi.fn(),
  useEditMarketplaceMovement: vi.fn(),
  useUndoMarketplaceMovement: vi.fn(),
}))

let canManage = true
vi.mock("@/lib/auth-context", () => ({
  useAuth: () => ({ hasPermission: () => canManage }),
}))

const mockedHistory = vi.mocked(useProductMarketplaceHistory)
const mockedEdit = vi.mocked(useEditMarketplaceMovement)
const mockedUndo = vi.mocked(useUndoMarketplaceMovement)

function row(over: Partial<ProductMarketplaceMovement>): ProductMarketplaceMovement {
  return {
    id: "m1",
    product_id: "prod-1",
    catalog_variant_id: null,
    platform: "amazon",
    platform_label: "Amazon",
    movement_type: "sale",
    quantity_packets: 20,
    quantity_delta: -20,
    quantity_after: -20,
    stock_date: "2026-09-21",
    reason: "Marketplace sale",
    actor_user_id: "u1",
    actor_label: "Komal",
    created_at: "2026-09-21T10:00:00Z",
    reverses_movement_id: null,
    replaces_movement_id: null,
    edited_from_packets: null,
    status: "active",
    can_edit: true,
    can_undo: true,
    ...over,
  }
}

function withRows(rows: ProductMarketplaceMovement[]) {
  mockedHistory.mockReturnValue({
    isLoading: false,
    isError: false,
    error: null,
    data: { data: rows, meta: { page: 1, page_size: 20, total: rows.length, total_pages: 1 } },
    refetch: vi.fn(),
  } as never)
}

function stubMutations() {
  const edit = vi.fn(async () => ({}))
  const undo = vi.fn(async () => ({}))
  mockedEdit.mockReturnValue({ mutateAsync: edit, isPending: false } as never)
  mockedUndo.mockReturnValue({ mutateAsync: undo, isPending: false } as never)
  return { edit, undo }
}

beforeEach(() => {
  canManage = true
})

describe("ProductMarketplaceHistory — audit trail, status and actions", () => {
  it("shows a Sale, its Reversal and the replacement as separate rows with their status", () => {
    stubMutations()
    withRows([
      row({
        id: "rev",
        movement_type: "reversal",
        quantity_delta: 20,
        status: "reversal",
        can_edit: false,
        can_undo: false,
        reverses_movement_id: "m1",
      }),
      row({ id: "m1", status: "undone", can_edit: false, can_undo: false }),
      row({
        id: "rep",
        quantity_packets: 15,
        quantity_delta: -15,
        replaces_movement_id: "m1",
        edited_from_packets: 20,
      }),
    ])
    renderWithProviders(<ProductMarketplaceHistory productId="prod-1" />)

    expect(screen.getByText("-20 packets")).toBeInTheDocument() // original kept
    expect(screen.getByText("+20 packets")).toBeInTheDocument() // reversal
    expect(screen.getByText("-15 packets")).toBeInTheDocument() // replacement
    expect(screen.getAllByText("Reversal")).toHaveLength(2) // movement type + status
    expect(screen.getByText("Undone")).toBeInTheDocument()
    expect(screen.getByText("Edited from 20 → 15 packets")).toBeInTheDocument()
  })

  it("offers Edit and Undo only on an effective manual movement", () => {
    stubMutations()
    withRows([
      row({ id: "a" }),
      row({ id: "b", status: "undone", can_edit: false, can_undo: false }),
      row({ id: "c", movement_type: "reversal", status: "reversal", can_edit: false, can_undo: false }),
    ])
    renderWithProviders(<ProductMarketplaceHistory productId="prod-1" />)

    expect(screen.getAllByRole("button", { name: "Edit" })).toHaveLength(1)
    expect(screen.getAllByRole("button", { name: "Undo" })).toHaveLength(1)
  })

  it("hides Edit and Undo for a user without inventory.manage", () => {
    canManage = false
    stubMutations()
    withRows([row({ id: "a" })])
    renderWithProviders(<ProductMarketplaceHistory productId="prod-1" />)

    expect(screen.queryByRole("button", { name: "Edit" })).not.toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "Undo" })).not.toBeInTheDocument()
  })

  it("scopes the query to one OMS variant when given a catalogVariantId and shows its title", () => {
    stubMutations()
    withRows([])
    renderWithProviders(
      <ProductMarketplaceHistory
        productId="prod-1"
        catalogVariantId="cv-gold"
        title="Gold Packet Marketplace History"
      />
    )

    expect(screen.getByText("Gold Packet Marketplace History")).toBeInTheDocument()
    expect(mockedHistory).toHaveBeenLastCalledWith(
      "prod-1",
      expect.objectContaining({ catalog_variant_id: "cv-gold" })
    )
  })
})

describe("Edit Marketplace Movement dialog", () => {
  async function openEdit() {
    const user = userEvent.setup()
    const { edit } = stubMutations()
    withRows([row({ id: "m1" })])
    renderWithProviders(<ProductMarketplaceHistory productId="prod-1" />)
    await user.click(screen.getByRole("button", { name: "Edit" }))
    return { user, edit, dialog: screen.getByRole("dialog") }
  }

  it("shows platform, movement type and current quantity read-only, with no SKU field", async () => {
    const { dialog } = await openEdit()

    expect(within(dialog).getByText("Edit Marketplace Movement")).toBeInTheDocument()
    expect(within(dialog).getByText("Amazon")).toBeInTheDocument()
    expect(within(dialog).getByText("Sale")).toBeInTheDocument()
    expect(within(dialog).getByText("20 packets")).toBeInTheDocument()
    expect(within(dialog).queryByText(/sku/i)).not.toBeInTheDocument()
    expect(within(dialog).queryByRole("combobox")).not.toBeInTheDocument()
  })

  it("keeps Save disabled until the quantity differs and a reason is given", async () => {
    const { user, dialog } = await openEdit()
    const save = within(dialog).getByRole("button", { name: "Save" })
    expect(save).toBeDisabled()

    await user.type(within(dialog).getByLabelText("New Quantity"), "20") // unchanged
    await user.type(within(dialog).getByLabelText("Reason"), "typo")
    expect(save).toBeDisabled()

    await user.clear(within(dialog).getByLabelText("New Quantity"))
    await user.type(within(dialog).getByLabelText("New Quantity"), "15")
    expect(save).toBeEnabled()

    await user.clear(within(dialog).getByLabelText("Reason"))
    expect(save).toBeDisabled()
  })

  it("submits only the new quantity and reason (20 → 15 packets)", async () => {
    const { user, edit, dialog } = await openEdit()
    await user.type(within(dialog).getByLabelText("New Quantity"), "15")
    await user.type(within(dialog).getByLabelText("Reason"), "Counted wrong")
    await user.click(within(dialog).getByRole("button", { name: "Save" }))

    expect(edit).toHaveBeenCalledWith({ quantity_packets: 15, reason: "Counted wrong" })
  })

  it("Cancel closes without saving", async () => {
    const { user, edit, dialog } = await openEdit()
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }))

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument()
    expect(edit).not.toHaveBeenCalled()
  })
})

describe("Undo Marketplace Movement dialog", () => {
  it("requires a reason, then submits an undo", async () => {
    const user = userEvent.setup()
    const { undo } = stubMutations()
    withRows([row({ id: "m1" })])
    renderWithProviders(<ProductMarketplaceHistory productId="prod-1" />)

    await user.click(screen.getByRole("button", { name: "Undo" }))
    const dialog = screen.getByRole("dialog")
    expect(within(dialog).getByText("Undo Marketplace Movement")).toBeInTheDocument()
    const confirm = within(dialog).getByRole("button", { name: "Undo" })
    expect(confirm).toBeDisabled()

    await user.type(within(dialog).getByLabelText("Reason"), "entered by mistake")
    expect(confirm).toBeEnabled()
    await user.click(confirm)

    expect(undo).toHaveBeenCalledWith({ reason: "entered by mistake" })
  })
})

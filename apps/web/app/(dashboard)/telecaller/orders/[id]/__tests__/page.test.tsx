import { describe, expect, it, vi } from "vitest"
import { screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { renderWithProviders } from "@/test-utils/render-with-providers"
import TelecallerOrderDetailPage from "@/app/(dashboard)/telecaller/orders/[id]/page"
import {
  useCallHistory,
  useConfirmOrder,
  useDeleteCallAttempt,
  useEditCallAttempt,
  useLogCall,
  useMyOrder,
  useScheduleFollowUp,
  useUnconfirmOrder,
  useUpdateOrderAddress,
} from "@/services/telecaller"

vi.mock("next/navigation", () => ({
  useParams: () => ({ id: "order-1" }),
}))

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn() },
}))

vi.mock("@/services/telecaller", () => ({
  useMyOrder: vi.fn(),
  useCallHistory: vi.fn(),
  useLogCall: vi.fn(),
  useScheduleFollowUp: vi.fn(),
  useEditCallAttempt: vi.fn(),
  useDeleteCallAttempt: vi.fn(),
  useConfirmOrder: vi.fn(),
  useUnconfirmOrder: vi.fn(),
  useUpdateOrderAddress: vi.fn(),
}))

const mockedUseMyOrder = vi.mocked(useMyOrder)
const mockedUseCallHistory = vi.mocked(useCallHistory)
const mockedUseLogCall = vi.mocked(useLogCall)
const mockedUseScheduleFollowUp = vi.mocked(useScheduleFollowUp)
const mockedUseEditCallAttempt = vi.mocked(useEditCallAttempt)
const mockedUseDeleteCallAttempt = vi.mocked(useDeleteCallAttempt)
const mockedUseConfirmOrder = vi.mocked(useConfirmOrder)
const mockedUseUnconfirmOrder = vi.mocked(useUnconfirmOrder)
const mockedUseUpdateOrderAddress = vi.mocked(useUpdateOrderAddress)

const ORDER = {
  order_id: "order-1",
  order_number: "OMS-0001",
  customer_name: "Alice",
  customer_phone: "9990000001",
  item_summary: "Ashwagandha",
  total_amount: "499.00",
  payment_type: "prepaid",
  payment_status: "paid",
  status: "pending",
  fulfillment_status: "unfulfilled",
  confirmed_at: null,
  confirmed_by_telecaller_id: null,
  order_datetime: "2026-08-01T00:00:00Z",
  shipping_address: {
    line1: "221B New Colony Road",
    line2: null,
    city: "Pune",
    state: "Maharashtra",
    pin_code: "411001",
    country: "India",
    contact_name: "Alice",
    contact_phone: "9990000001",
  },
  shipping_address_sync_status: "synced",
  shipping_address_sync_error: null,
  items: [
    {
      id: "item-1",
      sku: "AW-HM-PN-100g",
      product_name: "Ayush Wellness Herbal Masala",
      variant_title: "Pan Masala Flavor / 100 Grams Pouches",
      image_url: "https://cdn.shopify.com/example.jpg",
      quantity: 2,
      unit_price: "249.50",
      total_amount: "499.00",
    },
  ],
  assignment_id: "assign-1",
  assigned_to: "tc-1",
  assigned_to_name: "Telecaller One",
  call_status: "not_received",
  attempt_count: 1,
  last_attempt_at: "2026-08-27T11:32:00Z",
  next_follow_up_at: null,
}

const CALL_HISTORY = [
  {
    id: "call-1",
    order_id: "order-1",
    telecaller_id: "tc-1",
    attempt_number: 1,
    attempted_at: "2026-08-27T11:32:00Z",
    outcome: "not_received",
    notes: null,
    next_follow_up_at: null,
    created_at: "2026-08-27T11:32:00Z",
    is_edited: false,
  },
]

function mockCommonHooks() {
  mockedUseMyOrder.mockReturnValue({
    isLoading: false,
    isError: false,
    error: null,
    data: ORDER,
    refetch: vi.fn(),
  } as unknown as ReturnType<typeof useMyOrder>)
  mockedUseCallHistory.mockReturnValue({
    isLoading: false,
    isError: false,
    error: null,
    data: CALL_HISTORY,
    refetch: vi.fn(),
  } as unknown as ReturnType<typeof useCallHistory>)
  mockedUseScheduleFollowUp.mockReturnValue({
    mutate: vi.fn(),
    isPending: false,
  } as unknown as ReturnType<typeof useScheduleFollowUp>)
  mockedUseConfirmOrder.mockReturnValue({
    mutate: vi.fn(),
    isPending: false,
  } as unknown as ReturnType<typeof useConfirmOrder>)
  mockedUseUnconfirmOrder.mockReturnValue({
    mutate: vi.fn(),
    isPending: false,
  } as unknown as ReturnType<typeof useUnconfirmOrder>)
  mockedUseUpdateOrderAddress.mockReturnValue({
    mutate: vi.fn(),
    isPending: false,
  } as unknown as ReturnType<typeof useUpdateOrderAddress>)
}

describe("TelecallerOrderDetailPage", () => {
  it("renders order + call management info and submits a logged call", async () => {
    const user = userEvent.setup()
    const mutate = vi.fn()

    mockCommonHooks()
    mockedUseLogCall.mockReturnValue({
      mutate,
      isPending: false,
    } as unknown as ReturnType<typeof useLogCall>)
    mockedUseEditCallAttempt.mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useEditCallAttempt>)
    mockedUseDeleteCallAttempt.mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useDeleteCallAttempt>)

    renderWithProviders(<TelecallerOrderDetailPage />)

    expect(screen.getByText("Alice")).toBeInTheDocument()
    expect(screen.getByText("Attempt #1")).toBeInTheDocument()

    await user.click(screen.getByRole("button", { name: /^Log Call$/i }))
    await user.type(screen.getByPlaceholderText("Notes (optional)"), "Customer answered.")
    await user.click(screen.getByRole("button", { name: /^Save$/i }))

    expect(mutate).toHaveBeenCalledWith(
      { outcome: "connected", notes: "Customer answered.", next_follow_up_at: undefined },
      expect.anything()
    )
  }, 15000)

  it("logs a quick-status call with no dialog for Mark Confirmed", async () => {
    const user = userEvent.setup()
    const mutate = vi.fn()

    mockCommonHooks()
    mockedUseLogCall.mockReturnValue({
      mutate,
      isPending: false,
    } as unknown as ReturnType<typeof useLogCall>)
    mockedUseEditCallAttempt.mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useEditCallAttempt>)
    mockedUseDeleteCallAttempt.mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useDeleteCallAttempt>)

    renderWithProviders(<TelecallerOrderDetailPage />)

    await user.click(screen.getByRole("button", { name: /Mark Confirmed/i }))
    expect(mutate).toHaveBeenCalledWith({ outcome: "confirmed" }, expect.anything())
  })

  it("edits a call attempt via the Edit dialog, pre-filled with its current values", async () => {
    const user = userEvent.setup()
    const editMutate = vi.fn()

    mockCommonHooks()
    mockedUseLogCall.mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useLogCall>)
    mockedUseEditCallAttempt.mockReturnValue({
      mutate: editMutate,
      isPending: false,
    } as unknown as ReturnType<typeof useEditCallAttempt>)
    mockedUseDeleteCallAttempt.mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useDeleteCallAttempt>)

    renderWithProviders(<TelecallerOrderDetailPage />)

    await user.click(screen.getByRole("button", { name: /^Edit call attempt$/i }))
    expect(screen.getByText("Edit Attempt #1")).toBeInTheDocument()

    await user.click(screen.getByRole("button", { name: /^Save$/i }))

    expect(editMutate).toHaveBeenCalledWith(
      {
        attemptId: "call-1",
        input: {
          outcome: "not_received",
          notes: undefined,
          next_follow_up_at: undefined,
        },
      },
      expect.anything()
    )
  })

  it("deletes a call attempt after confirming in the alert dialog", async () => {
    const user = userEvent.setup()
    const deleteMutate = vi.fn()

    mockCommonHooks()
    mockedUseLogCall.mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useLogCall>)
    mockedUseEditCallAttempt.mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useEditCallAttempt>)
    mockedUseDeleteCallAttempt.mockReturnValue({
      mutate: deleteMutate,
      isPending: false,
    } as unknown as ReturnType<typeof useDeleteCallAttempt>)

    renderWithProviders(<TelecallerOrderDetailPage />)

    await user.click(screen.getByRole("button", { name: /^Delete call attempt$/i }))
    expect(screen.getByText("Delete this call attempt?")).toBeInTheDocument()

    await user.click(screen.getByRole("button", { name: /^Delete$/i }))
    expect(deleteMutate).toHaveBeenCalledWith("call-1", expect.anything())
  })

  it("shows a Confirm Order action for a pending order, separate from call outcome", async () => {
    const user = userEvent.setup()
    const confirmMutate = vi.fn((_vars, opts) => opts.onSuccess())
    const logCallMutate = vi.fn()

    mockCommonHooks()
    mockedUseLogCall.mockReturnValue({
      mutate: logCallMutate,
      isPending: false,
    } as unknown as ReturnType<typeof useLogCall>)
    mockedUseConfirmOrder.mockReturnValue({
      mutate: confirmMutate,
      isPending: false,
    } as unknown as ReturnType<typeof useConfirmOrder>)

    renderWithProviders(<TelecallerOrderDetailPage />)

    expect(screen.getByText("Confirm Order for Shipment")).toBeInTheDocument()
    await user.click(screen.getByRole("button", { name: /^Confirm Order for Shipment$/i }))
    expect(screen.getByText("Confirm this order?")).toBeInTheDocument()

    await user.click(screen.getByRole("button", { name: /^Confirm Order$/i }))
    expect(confirmMutate).toHaveBeenCalledWith(undefined, expect.anything())

    // Marking a call outcome must never call the order-confirm mutation.
    await user.click(screen.getByRole("button", { name: /Mark Confirmed/i }))
    expect(logCallMutate).toHaveBeenCalledWith({ outcome: "confirmed" }, expect.anything())
    expect(confirmMutate).toHaveBeenCalledTimes(1)
  })

  it("hides Confirm Order once the order is already confirmed", () => {
    mockCommonHooks()
    mockedUseMyOrder.mockReturnValue({
      isLoading: false,
      isError: false,
      error: null,
      data: { ...ORDER, status: "confirmed" },
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useMyOrder>)
    mockedUseLogCall.mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useLogCall>)

    renderWithProviders(<TelecallerOrderDetailPage />)

    expect(screen.queryByText("Confirm Order for Shipment")).not.toBeInTheDocument()
  })

  it("shows a Revert to Pending action for a confirmed order and calls unconfirm", async () => {
    const user = userEvent.setup()
    const unconfirmMutate = vi.fn((_vars, opts) => opts.onSuccess())

    mockCommonHooks()
    mockedUseMyOrder.mockReturnValue({
      isLoading: false,
      isError: false,
      error: null,
      data: { ...ORDER, status: "confirmed" },
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useMyOrder>)
    mockedUseLogCall.mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useLogCall>)
    mockedUseUnconfirmOrder.mockReturnValue({
      mutate: unconfirmMutate,
      isPending: false,
    } as unknown as ReturnType<typeof useUnconfirmOrder>)

    renderWithProviders(<TelecallerOrderDetailPage />)

    expect(screen.getByText("Revert to Pending")).toBeInTheDocument()
    await user.click(screen.getByRole("button", { name: /^Revert to Pending$/i }))
    expect(screen.getByText("Revert this order to pending?")).toBeInTheDocument()

    await user.click(screen.getByRole("button", { name: /^Revert Order$/i }))
    expect(unconfirmMutate).toHaveBeenCalledWith(undefined, expect.anything())
  })

  it("hides Revert to Pending for a pending order", () => {
    mockCommonHooks()
    mockedUseLogCall.mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useLogCall>)

    renderWithProviders(<TelecallerOrderDetailPage />)

    expect(screen.queryByText("Revert to Pending")).not.toBeInTheDocument()
  })

  it("shows product image, variant title, and SKU for each line item", () => {
    mockCommonHooks()
    mockedUseLogCall.mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useLogCall>)

    renderWithProviders(<TelecallerOrderDetailPage />)

    expect(screen.getByText("Ayush Wellness Herbal Masala")).toBeInTheDocument()
    expect(screen.getByText("Pan Masala Flavor / 100 Grams Pouches")).toBeInTheDocument()
    expect(screen.getByText("SKU: AW-HM-PN-100g")).toBeInTheDocument()
    const image = screen.getByRole("img")
    expect(image).toHaveAttribute("src", "https://cdn.shopify.com/example.jpg")
  })

  it("opens the Edit Address dialog pre-filled with the current address and saves", async () => {
    const user = userEvent.setup()
    const updateMutate = vi.fn((_vars, opts) =>
      opts.onSuccess({ ...ORDER, shipping_address_sync_status: "synced" })
    )

    mockCommonHooks()
    mockedUseLogCall.mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useLogCall>)
    mockedUseUpdateOrderAddress.mockReturnValue({
      mutate: updateMutate,
      isPending: false,
    } as unknown as ReturnType<typeof useUpdateOrderAddress>)

    renderWithProviders(<TelecallerOrderDetailPage />)

    await user.click(screen.getByRole("button", { name: /^Edit Address$/i }))
    expect(
      screen.getByText("Updates the shipping address in OMS and on the corresponding Shopify order.")
    ).toBeInTheDocument()
    expect(screen.getByDisplayValue("221B New Colony Road")).toBeInTheDocument()
    expect(screen.getByDisplayValue("Pune")).toBeInTheDocument()

    await user.click(screen.getByRole("button", { name: /^Save Address$/i }))
    expect(updateMutate).toHaveBeenCalledWith(
      expect.objectContaining({ line1: "221B New Colony Road", city: "Pune" }),
      expect.anything()
    )
  })

  it("shows a warning instead of success when the address saves but Shopify sync fails", async () => {
    const user = userEvent.setup()
    const { toast } = await import("sonner")
    const updateMutate = vi.fn((_vars, opts) =>
      opts.onSuccess({
        ...ORDER,
        shipping_address_sync_status: "failed",
        shipping_address_sync_error: "Shopify is down.",
      })
    )

    mockCommonHooks()
    mockedUseLogCall.mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useLogCall>)
    mockedUseUpdateOrderAddress.mockReturnValue({
      mutate: updateMutate,
      isPending: false,
    } as unknown as ReturnType<typeof useUpdateOrderAddress>)

    renderWithProviders(<TelecallerOrderDetailPage />)

    await user.click(screen.getByRole("button", { name: /^Edit Address$/i }))
    await user.click(screen.getByRole("button", { name: /^Save Address$/i }))

    expect(toast.warning).toHaveBeenCalledWith(
      expect.stringContaining("Shopify sync failed")
    )
  })

  it("shows the Address Validation badge for the order's shipping address", () => {
    mockedUseMyOrder.mockReturnValue({
      isLoading: false,
      isError: false,
      error: null,
      data: {
        ...ORDER,
        shipping_address_validation_status: "junk",
        shipping_address_validation_score: 24,
      },
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useMyOrder>)
    mockedUseCallHistory.mockReturnValue({
      isLoading: false,
      isError: false,
      error: null,
      data: CALL_HISTORY,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useCallHistory>)
    mockedUseScheduleFollowUp.mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useScheduleFollowUp>)
    mockedUseConfirmOrder.mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useConfirmOrder>)
    mockedUseUnconfirmOrder.mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useUnconfirmOrder>)
    mockedUseUpdateOrderAddress.mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useUpdateOrderAddress>)
    mockedUseLogCall.mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useLogCall>)

    renderWithProviders(<TelecallerOrderDetailPage />)

    expect(screen.getByText("24%")).toBeInTheDocument()
    expect(screen.getByText("Junk Address")).toBeInTheDocument()
  })
})

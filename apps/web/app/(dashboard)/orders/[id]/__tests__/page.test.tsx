import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { renderWithProviders } from "@/test-utils/render-with-providers"
import OrderDetailPage from "@/app/(dashboard)/orders/[id]/page"
import { toast } from "sonner"
import {
  useOrder,
  useOrderTimeline,
  useProcessExistingShipments,
  useValidateOrderAddress,
} from "@/services/orders"
import { usePaymentsForOrder } from "@/services/payments"
import { useShipmentsForOrder } from "@/services/shipments"
import { useReturnsForOrder } from "@/services/returns"
import { useRefundsForOrder } from "@/services/refunds"
import { useAuth } from "@/lib/auth-context"
import type { OrderDetail } from "@/types/order"
import type { ProcessExistingShipmentResult } from "@/types/shipment"

vi.mock("next/navigation", () => ({
  useParams: () => ({ id: "order-1" }),
  useSearchParams: () => new URLSearchParams(),
}))

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn() },
}))

vi.mock("@/services/orders", () => ({
  useOrder: vi.fn(),
  useOrderTimeline: vi.fn(),
  useTransitionOrderStatus: () => ({ mutate: vi.fn(), isPending: false }),
  useProcessExistingShipments: vi.fn(),
  useValidateOrderAddress: vi.fn(),
}))

vi.mock("@/services/payments", () => ({
  usePaymentsForOrder: vi.fn(),
}))

vi.mock("@/services/shipments", () => ({
  useShipmentsForOrder: vi.fn(),
}))

vi.mock("@/services/returns", () => ({
  useReturnsForOrder: vi.fn(),
}))

vi.mock("@/services/refunds", () => ({
  useRefundsForOrder: vi.fn(),
}))

vi.mock("@/lib/auth-context", () => ({
  useAuth: vi.fn(),
}))

vi.mock("@/components/orders/cashfree-payment-card", () => ({
  CashfreePaymentCard: () => null,
}))

const mockedUseOrder = vi.mocked(useOrder)
const mockedUseOrderTimeline = vi.mocked(useOrderTimeline)
const mockedUsePaymentsForOrder = vi.mocked(usePaymentsForOrder)
const mockedUseShipmentsForOrder = vi.mocked(useShipmentsForOrder)
const mockedUseReturnsForOrder = vi.mocked(useReturnsForOrder)
const mockedUseRefundsForOrder = vi.mocked(useRefundsForOrder)
const mockedUseAuth = vi.mocked(useAuth)
const mockedUseProcessExistingShipments = vi.mocked(useProcessExistingShipments)
const mockedUseValidateOrderAddress = vi.mocked(useValidateOrderAddress)

type ProcessOpts = {
  onSuccess?: (r: {
    processed_count: number
    skipped_count: number
    failed_count: number
    results: ProcessExistingShipmentResult[]
  }) => void
  onError?: (e: unknown) => void
}

/** Default: the order's Shiprocket shipment can't be located -- individual
 * tests override `mutate` for the success/skipped paths. This hook does
 * the whole resolve+assign server-side now -- no separate "locate" step
 * on the frontend.
 */
function mockProcessShipments(
  impl: (orderIds: string[], opts?: ProcessOpts) => void = (orderIds, opts) =>
    opts?.onSuccess?.({
      processed_count: 0,
      skipped_count: 0,
      failed_count: orderIds.length,
      results: orderIds.map((id) => ({
        order_id: id,
        order_number: null,
        status: "failed",
        shiprocket_shipment_id: null,
        shiprocket_order_id: null,
        awb: null,
        courier_name: null,
        reason: "Existing Shiprocket order could not be located for this order.",
      })),
    })
) {
  mockedUseProcessExistingShipments.mockReturnValue({
    mutate: vi.fn(impl),
    isPending: false,
  } as unknown as ReturnType<typeof useProcessExistingShipments>)
}

function emptyListQuery<T>(data: T[] = []) {
  return {
    isLoading: false,
    isError: false,
    error: null,
    data,
    refetch: vi.fn(),
  } as unknown as T
}

const BASE_ORDER: OrderDetail = {
  id: "order-1",
  order_number: "AWL81350",
  shopify_order_id: "500",
  customer_id: null,
  order_datetime: "2026-08-01T00:00:00Z",
  currency: "INR",
  subtotal: "500.00",
  discount_amount: "0.00",
  tax_amount: "0.00",
  shipping_charge: "0.00",
  total_amount: "500.00",
  payment_type: "prepaid",
  payment_status: "paid",
  status: "confirmed",
  fulfillment_status: "unfulfilled",
  cancellation_status: "none",
  notes: null,
  shopify_tags: null,
  shopify_order_note: null,
  shopify_shipment_status: null,
  shipping_address: null,
  billing_address: null,
  source_system: "shopify",
  confirmed_by_telecaller_id: null,
  confirmed_at: null,
  created_at: "2026-08-01T00:00:00Z",
  updated_at: "2026-08-01T00:00:00Z",
  items: [],
  customer: null,
  confirmed_by_telecaller_name: null,
}

function setUpQueries(order: OrderDetail) {
  mockProcessShipments()
  mockedUseValidateOrderAddress.mockReturnValue({
    mutate: vi.fn(),
    isPending: false,
  } as unknown as ReturnType<typeof useValidateOrderAddress>)
  mockedUseOrder.mockReturnValue({
    isLoading: false,
    isError: false,
    error: null,
    data: order,
    refetch: vi.fn(),
  } as unknown as ReturnType<typeof useOrder>)
  mockedUseOrderTimeline.mockReturnValue(
    emptyListQuery([]) as unknown as ReturnType<typeof useOrderTimeline>
  )
  mockedUsePaymentsForOrder.mockReturnValue(
    emptyListQuery([]) as unknown as ReturnType<typeof usePaymentsForOrder>
  )
  mockedUseShipmentsForOrder.mockReturnValue(
    emptyListQuery([]) as unknown as ReturnType<typeof useShipmentsForOrder>
  )
  mockedUseReturnsForOrder.mockReturnValue(
    emptyListQuery([]) as unknown as ReturnType<typeof useReturnsForOrder>
  )
  mockedUseRefundsForOrder.mockReturnValue(
    emptyListQuery([]) as unknown as ReturnType<typeof useRefundsForOrder>
  )
  mockedUseAuth.mockReturnValue({
    hasPermission: () => false,
  } as unknown as ReturnType<typeof useAuth>)
}

describe("OrderDetailPage — Shopify tags and order note", () => {
  it("renders Shopify tags as badges and the order note in quotes", () => {
    setUpQueries({
      ...BASE_ORDER,
      shopify_tags: ["Prepaid", "VIP"],
      shopify_order_note: "Please deliver after 6 PM",
    })

    renderWithProviders(<OrderDetailPage />)

    expect(screen.getByText("Shopify Tags & Order Note")).toBeInTheDocument()
    expect(screen.getByText("Prepaid")).toBeInTheDocument()
    expect(screen.getByText("VIP")).toBeInTheDocument()
    expect(screen.getByText("“Please deliver after 6 PM”")).toBeInTheDocument()
  })

  it("omits the section entirely when there are no tags and no note", () => {
    setUpQueries({ ...BASE_ORDER, shopify_tags: [], shopify_order_note: null })

    renderWithProviders(<OrderDetailPage />)

    expect(screen.queryByText("Shopify Tags & Order Note")).not.toBeInTheDocument()
  })

  it("omits the section for a manually created order with no Shopify data", () => {
    setUpQueries({ ...BASE_ORDER, shopify_tags: null, shopify_order_note: null })

    renderWithProviders(<OrderDetailPage />)

    expect(screen.queryByText("Shopify Tags & Order Note")).not.toBeInTheDocument()
  })

  it("renders the note without any tags when only a note is present", () => {
    setUpQueries({ ...BASE_ORDER, shopify_tags: [], shopify_order_note: "Leave at the gate" })

    renderWithProviders(<OrderDetailPage />)

    expect(screen.getByText("Shopify Tags & Order Note")).toBeInTheDocument()
    expect(screen.getByText("“Leave at the gate”")).toBeInTheDocument()
  })
})

describe("OrderDetailPage — Ship Order (no shipment yet)", () => {
  let openSpy: ReturnType<typeof vi.spyOn>
  let clipboardSpy: ReturnType<typeof vi.fn>

  beforeEach(() => {
    openSpy = vi.spyOn(window, "open").mockReturnValue(null)
    // No clipboard dependency exists for this workflow -- stubbed only to
    // detect an unexpected call, never relied on for the flow to work.
    clipboardSpy = vi.fn()
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText: clipboardSpy },
      configurable: true,
    })
    mockProcessShipments()
  })

  afterEach(() => {
    openSpy.mockRestore()
    vi.clearAllMocks()
  })

  it("never calls a Shiprocket create-shipment API -- when no existing shipment can be resolved, the dialog shows why", async () => {
    const user = userEvent.setup()
    setUpQueries(BASE_ORDER)
    mockedUseAuth.mockReturnValue({
      hasPermission: () => true,
    } as unknown as ReturnType<typeof useAuth>)

    renderWithProviders(<OrderDetailPage />)

    const shipButton = screen.getByRole("button", { name: /^Ship Order$/i })
    await user.click(shipButton)

    // The click calls the process-shipment endpoint (never a
    // create-shipment call); when the order's Shiprocket shipment can't
    // be located, the dialog reports the real reason -- never a guess.
    const dialog = await screen.findByRole("dialog")
    expect(
      within(dialog).getByText(/Existing Shiprocket order could not be located/i)
    ).toBeInTheDocument()
    expect(clipboardSpy).not.toHaveBeenCalled()
  })

  it("shows the processing state, then courier + AWB on success, with no automatic clipboard write", async () => {
    const user = userEvent.setup()
    setUpQueries(BASE_ORDER)
    mockProcessShipments((orderIds, opts) =>
      opts?.onSuccess?.({
        processed_count: 1,
        skipped_count: 0,
        failed_count: 0,
        results: orderIds.map((id) => ({
          order_id: id,
          order_number: "AWL81350",
          status: "success",
          shiprocket_shipment_id: "7001",
          shiprocket_order_id: "1900007001",
          awb: "AWB90001",
          courier_name: "Delhivery",
          reason: null,
        })),
      })
    )
    mockedUseAuth.mockReturnValue({
      hasPermission: () => true,
    } as unknown as ReturnType<typeof useAuth>)

    renderWithProviders(<OrderDetailPage />)

    await user.click(screen.getByRole("button", { name: /^Ship Order$/i }))

    const dialog = await screen.findByRole("dialog")
    expect(within(dialog).getByText(/Courier: Delhivery/i)).toBeInTheDocument()
    expect(within(dialog).getByText(/AWB: AWB90001/i)).toBeInTheDocument()
    expect(clipboardSpy).not.toHaveBeenCalled()
  })

  it("opens the plain Ready to Ship page from the dialog, with no order_ids query parameter", async () => {
    const user = userEvent.setup()
    setUpQueries(BASE_ORDER)
    mockedUseAuth.mockReturnValue({
      hasPermission: () => true,
    } as unknown as ReturnType<typeof useAuth>)

    renderWithProviders(<OrderDetailPage />)

    await user.click(screen.getByRole("button", { name: /^Ship Order$/i }))
    const dialog = await screen.findByRole("dialog")

    await user.click(
      within(dialog).getByRole("button", { name: /^Open Shiprocket Ready to Ship$/i })
    )

    expect(openSpy).toHaveBeenCalledWith(
      "https://app.shiprocket.in/seller/orders/readytoship",
      "_blank",
      "noopener,noreferrer"
    )
    const [openedUrl] = openSpy.mock.calls[0]
    expect(String(openedUrl)).not.toContain("?")
    expect(String(openedUrl)).not.toContain("order_ids")
  })

  it("shows a failure reason from a request-level error, never a silent failure", async () => {
    const user = userEvent.setup()
    setUpQueries(BASE_ORDER)
    mockProcessShipments((_orderIds, opts) => opts?.onError?.(new Error("Network unreachable.")))
    mockedUseAuth.mockReturnValue({
      hasPermission: () => true,
    } as unknown as ReturnType<typeof useAuth>)

    renderWithProviders(<OrderDetailPage />)

    await user.click(screen.getByRole("button", { name: /^Ship Order$/i }))

    const dialog = await screen.findByRole("dialog")
    expect(within(dialog).getByText(/Network unreachable\./i)).toBeInTheDocument()
    expect(toast.error).not.toHaveBeenCalled()
  })

  it("hides Ship Order entirely without shipments.update permission", () => {
    setUpQueries(BASE_ORDER)
    mockedUseAuth.mockReturnValue({
      hasPermission: () => false,
    } as unknown as ReturnType<typeof useAuth>)

    renderWithProviders(<OrderDetailPage />)

    expect(screen.queryByRole("button", { name: /^Ship Order$/i })).not.toBeInTheDocument()
  })
})

const SAMPLE_ADDRESS = {
  contact_name: "Jane Doe",
  line1: "273 House No",
  line2: null,
  city: "Mandsaur",
  state: "Madhya Pradesh",
  pin_code: "458556",
  country: "India",
  contact_phone: null,
  is_default: false,
}

describe("OrderDetailPage — Address Validation card", () => {
  afterEach(() => vi.clearAllMocks())

  it("shows a Valid Address result with score, reason, and validated-at", () => {
    setUpQueries({
      ...BASE_ORDER,
      shipping_address: SAMPLE_ADDRESS,
      shipping_address_validation_status: "valid",
      shipping_address_validation_score: 95,
      shipping_address_validation_reason: "Address looks complete.",
      shipping_address_validated_at: "2026-08-01T12:00:00Z",
    })

    renderWithProviders(<OrderDetailPage />)

    expect(screen.getByText("95%")).toBeInTheDocument()
    expect(screen.getByText("Valid Address")).toBeInTheDocument()
    expect(screen.getByText("Address looks complete.")).toBeInTheDocument()
    expect(screen.getByText(/Last validated/i)).toBeInTheDocument()
  })

  it("shows an Ambiguous Address result", () => {
    setUpQueries({
      ...BASE_ORDER,
      shipping_address: { ...SAMPLE_ADDRESS, city: "", state: null },
      shipping_address_validation_status: "ambiguous",
      shipping_address_validation_score: 50,
      shipping_address_validation_reason: "City is missing; state is missing.",
      shipping_address_validated_at: "2026-08-01T12:00:00Z",
    })

    renderWithProviders(<OrderDetailPage />)

    expect(screen.getByText("50%")).toBeInTheDocument()
    expect(screen.getByText("Ambiguous Address")).toBeInTheDocument()
  })

  it("shows a Junk Address result", () => {
    setUpQueries({
      ...BASE_ORDER,
      shipping_address: {
        ...SAMPLE_ADDRESS,
        line1: "test address asdf",
        city: "xxxx",
        state: null,
        pin_code: "",
      },
      shipping_address_validation_status: "junk",
      shipping_address_validation_score: 24,
      shipping_address_validation_reason: "Address text matches a known junk pattern.",
      shipping_address_validated_at: "2026-08-01T12:00:00Z",
    })

    renderWithProviders(<OrderDetailPage />)

    expect(screen.getByText("24%")).toBeInTheDocument()
    expect(screen.getByText("Junk Address")).toBeInTheDocument()
  })

  it("shows Validation pending when an address exists but has never been validated", () => {
    setUpQueries({
      ...BASE_ORDER,
      shipping_address: SAMPLE_ADDRESS,
      shipping_address_validation_status: null,
      shipping_address_validation_score: null,
    })

    renderWithProviders(<OrderDetailPage />)

    expect(screen.getByText("Validation pending")).toBeInTheDocument()
  })

  it("shows an unavailable state when the provider could not classify the address", () => {
    setUpQueries({
      ...BASE_ORDER,
      shipping_address: SAMPLE_ADDRESS,
      shipping_address_validation_status: "unknown",
      shipping_address_validation_score: null,
      shipping_address_validation_reason: "Address validation provider raised an unexpected error.",
    })

    renderWithProviders(<OrderDetailPage />)

    expect(screen.getByText("Validation Unavailable")).toBeInTheDocument()
  })

  it("hides the Validate Address button without orders.update permission", () => {
    setUpQueries({
      ...BASE_ORDER,
      shipping_address: SAMPLE_ADDRESS,
    })
    mockedUseAuth.mockReturnValue({
      hasPermission: () => false,
    } as unknown as ReturnType<typeof useAuth>)

    renderWithProviders(<OrderDetailPage />)

    expect(
      screen.queryByRole("button", { name: /Validate Address/i })
    ).not.toBeInTheDocument()
  })

  it("triggers revalidation from the Validate Address button", async () => {
    const user = userEvent.setup()
    const mutate = vi.fn()
    setUpQueries({
      ...BASE_ORDER,
      shipping_address: SAMPLE_ADDRESS,
    })
    mockedUseValidateOrderAddress.mockReturnValue({
      mutate,
      isPending: false,
    } as unknown as ReturnType<typeof useValidateOrderAddress>)
    mockedUseAuth.mockReturnValue({
      hasPermission: () => true,
    } as unknown as ReturnType<typeof useAuth>)

    renderWithProviders(<OrderDetailPage />)

    await user.click(screen.getByRole("button", { name: /Validate Address/i }))

    expect(mutate).toHaveBeenCalled()
  })
})

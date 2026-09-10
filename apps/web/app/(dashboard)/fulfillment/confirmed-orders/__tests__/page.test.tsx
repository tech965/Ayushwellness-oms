import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { renderWithProviders } from "@/test-utils/render-with-providers"
import FulfillmentConfirmedOrdersPage from "@/app/(dashboard)/fulfillment/confirmed-orders/page"
import { toast } from "sonner"
import { useTelecallerConfirmedOrders } from "@/services/orders"
import { useLocateShiprocketOrders } from "@/services/shipment-queue"
import { useRetryShopifySync } from "@/services/shipments"
import { useTeamTelecallers } from "@/services/team"

const mockPush = vi.fn()

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockPush, replace: vi.fn(), back: vi.fn() }),
  usePathname: () => "/fulfillment/confirmed-orders",
  useSearchParams: () => new URLSearchParams(),
}))

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn() },
}))

vi.mock("@/services/orders", () => ({
  useTelecallerConfirmedOrders: vi.fn(),
}))

vi.mock("@/services/shipment-queue", () => ({
  useLocateShiprocketOrders: vi.fn(),
}))

vi.mock("@/services/shipments", () => ({
  useRetryShopifySync: vi.fn(),
}))

vi.mock("@/services/team", () => ({
  useTeamTelecallers: vi.fn(),
}))

const mockedUseTelecallerConfirmedOrders = vi.mocked(useTelecallerConfirmedOrders)
const mockedUseTeamTelecallers = vi.mocked(useTeamTelecallers)
const mockedUseLocateShiprocketOrders = vi.mocked(useLocateShiprocketOrders)
const mockedUseRetryShopifySync = vi.mocked(useRetryShopifySync)

const ROW = {
  id: "order-1",
  order_number: "AWL95408",
  customer_name: "Ranjit",
  customer_phone: "9990000001",
  item_summary: "Ayush Wellness Herbal Masala x1",
  total_amount: "1749.00",
  payment_type: "cod",
  payment_status: "paid",
  status: "confirmed",
  fulfillment_status: "unfulfilled",
  shipment_status: null as string | null,
  shipment_id: null as string | null,
  shopify_sync_status: null as string | null,
  shiprocket_order_url: null as string | null,
  confirmed_at: "2026-09-09T10:32:00Z",
  confirmed_by_telecaller_id: "tc-1",
  confirmed_by_telecaller_name: "Rahul Sharma",
}

function mockQuery(data: typeof ROW[] = [ROW]) {
  mockedUseTelecallerConfirmedOrders.mockReturnValue({
    isLoading: false,
    isError: false,
    error: null,
    data: {
      data,
      meta: { page: 1, page_size: 20, total_items: data.length, total_pages: 1 },
    },
    refetch: vi.fn(),
  } as unknown as ReturnType<typeof useTelecallerConfirmedOrders>)
  mockedUseTeamTelecallers.mockReturnValue({
    data: [{ telecaller_id: "tc-1", telecaller_name: "Rahul Sharma" }],
  } as unknown as ReturnType<typeof useTeamTelecallers>)
}

function mockShipHooks(
  locateImpl: (ids: string[], opts?: { onSuccess?: (r: unknown) => void }) => void = (ids, opts) =>
    opts?.onSuccess?.(
      ids.map((id) => ({
        order_id: id,
        status: "not_found",
        shiprocket_order_url: null,
        message: null,
      }))
    )
) {
  mockedUseLocateShiprocketOrders.mockReturnValue({
    mutate: vi.fn(locateImpl),
    data: undefined,
    isPending: false,
  } as unknown as ReturnType<typeof useLocateShiprocketOrders>)
  mockedUseRetryShopifySync.mockReturnValue({
    mutate: vi.fn(),
    isPending: false,
  } as unknown as ReturnType<typeof useRetryShopifySync>)
}

describe("FulfillmentConfirmedOrdersPage", () => {
  let openSpy: ReturnType<typeof vi.spyOn>

  beforeEach(() => {
    openSpy = vi.spyOn(window, "open").mockReturnValue(null)
  })

  afterEach(() => {
    openSpy.mockRestore()
    vi.clearAllMocks()
  })

  it("shows the page title and subtitle", () => {
    mockQuery()
    mockShipHooks()
    renderWithProviders(<FulfillmentConfirmedOrdersPage />)
    expect(screen.getByText("Confirmed by Telecaller")).toBeInTheDocument()
    expect(screen.getByText("1 orders confirmed by Telecalling.")).toBeInTheDocument()
  })

  it("always shows the real Telecaller name, never a dash, when confirmed_by_telecaller_id is set", () => {
    mockQuery()
    mockShipHooks()
    renderWithProviders(<FulfillmentConfirmedOrdersPage />)
    expect(screen.getByText("Rahul Sharma")).toBeInTheDocument()
  })

  it("renders order/customer/items/amount/status columns from the example row", () => {
    mockQuery()
    mockShipHooks()
    renderWithProviders(<FulfillmentConfirmedOrdersPage />)
    expect(screen.getByText("AWL95408")).toBeInTheDocument()
    expect(screen.getByText("Ranjit")).toBeInTheDocument()
    expect(screen.getByText("Ayush Wellness Herbal Masala x1")).toBeInTheDocument()
  })

  it("shows Ship Order directly in the row, opening the real Shiprocket order page", async () => {
    const user = userEvent.setup()
    mockQuery([
      {
        ...ROW,
        shiprocket_order_url: "https://app.shiprocket.in/seller/orders/details/1576398335",
      },
    ])
    mockShipHooks()

    renderWithProviders(<FulfillmentConfirmedOrdersPage />)

    await user.click(screen.getByRole("button", { name: /^Ship Order$/i }))
    expect(openSpy).toHaveBeenCalledWith(
      "https://app.shiprocket.in/seller/orders/details/1576398335",
      "_blank",
      "noopener,noreferrer"
    )
  })

  it("shows the unavailable message, never a Shiprocket API call, when no order id is stored", async () => {
    const user = userEvent.setup()
    mockQuery()
    mockShipHooks()

    renderWithProviders(<FulfillmentConfirmedOrdersPage />)

    await user.click(screen.getByRole("button", { name: /^Ship Order$/i }))
    expect(openSpy).not.toHaveBeenCalled()
    expect(toast.error).toHaveBeenCalledWith(
      "Shiprocket order link is unavailable for this order.",
      expect.anything()
    )
  })

  it("hides Ship Order once the order is already shipped/in transit/delivered", () => {
    mockQuery([
      {
        ...ROW,
        status: "shipped",
        fulfillment_status: "fulfilled",
        shipment_status: "delivered",
        shipment_id: "ship-1",
      },
    ])
    mockShipHooks()
    renderWithProviders(<FulfillmentConfirmedOrdersPage />)

    expect(screen.getByText("AWL95408")).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: /^Ship Order$/i })).not.toBeInTheDocument()
    expect(screen.getByRole("button", { name: /^View$/i })).toBeInTheDocument()
  })

  it("navigates to the order detail page from the View action, without filtering by shipment status", async () => {
    const user = userEvent.setup()
    mockQuery()
    mockShipHooks()
    renderWithProviders(<FulfillmentConfirmedOrdersPage />)
    await user.click(screen.getByRole("button", { name: /^View$/i }))
    expect(mockPush).toHaveBeenCalledWith("/orders/order-1")
  })

  it("shows a shipped order (not just orders still needing shipment) -- this is a history view", () => {
    mockQuery([
      { ...ROW, status: "shipped", fulfillment_status: "fulfilled", shipment_status: "delivered" },
    ])
    mockShipHooks()
    renderWithProviders(<FulfillmentConfirmedOrdersPage />)
    expect(screen.getByText("AWL95408")).toBeInTheDocument()
  })

  it("selects orders via checkbox and opens their existing Shiprocket order pages (never creates)", async () => {
    const user = userEvent.setup()
    const locateMutate = vi.fn()
    mockQuery()
    mockShipHooks()
    mockedUseLocateShiprocketOrders.mockReturnValue({
      mutate: locateMutate,
      data: [
        {
          order_id: "order-1",
          status: "found",
          shiprocket_order_url: "https://app.shiprocket.in/seller/orders/details/order-1",
          message: null,
        },
      ],
      isPending: false,
    } as unknown as ReturnType<typeof useLocateShiprocketOrders>)

    renderWithProviders(<FulfillmentConfirmedOrdersPage />)

    const checkboxes = screen.getAllByRole("checkbox")
    await user.click(checkboxes[1])
    expect(screen.getByText("Selected 1 orders")).toBeInTheDocument()

    await user.click(screen.getByRole("button", { name: /^Open in Shiprocket$/i }))
    expect(locateMutate).toHaveBeenCalledWith(["order-1"])

    const dialog = screen.getByRole("alertdialog")
    expect(
      within(dialog).queryByRole("button", { name: /create shipment/i })
    ).not.toBeInTheDocument()
    await user.click(within(dialog).getByRole("button", { name: /^Open All Found/i }))
    expect(openSpy).toHaveBeenCalledWith(
      "https://app.shiprocket.in/seller/orders/details/order-1",
      "_blank",
      "noopener,noreferrer"
    )
  })

  it("shows an empty state when nothing has been confirmed by Telecalling yet", () => {
    mockQuery([])
    mockShipHooks()
    renderWithProviders(<FulfillmentConfirmedOrdersPage />)
    expect(screen.getByText("No Telecaller-confirmed orders yet")).toBeInTheDocument()
  })
})

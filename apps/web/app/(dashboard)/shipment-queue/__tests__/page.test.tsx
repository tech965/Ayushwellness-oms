import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { renderWithProviders } from "@/test-utils/render-with-providers"
import ShipmentQueuePage from "@/app/(dashboard)/shipment-queue/page"
import { useProcessExistingShipments } from "@/services/orders"
import { useShipmentQueue } from "@/services/shipment-queue"
import { useRetryShopifySync } from "@/services/shipments"
import { useTeamTelecallers } from "@/services/team"
import type { ProcessExistingShipmentResult } from "@/types/shipment"

const mockPush = vi.fn()
const READY_TO_SHIP_URL = "https://app.shiprocket.in/seller/orders/readytoship"

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockPush, replace: vi.fn(), back: vi.fn() }),
  usePathname: () => "/shipment-queue",
  useSearchParams: () => new URLSearchParams(),
}))

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn() },
}))

vi.mock("@/services/shipment-queue", () => ({
  useShipmentQueue: vi.fn(),
}))

vi.mock("@/services/orders", () => ({
  useProcessExistingShipments: vi.fn(),
}))

vi.mock("@/services/shipments", () => ({
  useRetryShopifySync: vi.fn(),
}))

vi.mock("@/services/team", () => ({
  useTeamTelecallers: vi.fn(),
}))

const mockedUseShipmentQueue = vi.mocked(useShipmentQueue)
const mockedUseTeamTelecallers = vi.mocked(useTeamTelecallers)
const mockedUseProcessExistingShipments = vi.mocked(useProcessExistingShipments)
const mockedUseRetryShopifySync = vi.mocked(useRetryShopifySync)

const ROW = {
  order_id: "order-1",
  order_number: "OMS-0001",
  customer_name: "Alice",
  customer_phone: "9990000001",
  item_summary: "Ashwagandha",
  sku_summary: "ASH-001",
  total_quantity: 1,
  total_amount: "499.00",
  payment_type: "cod",
  payment_status: "pending",
  confirmed_at: "2026-09-01T00:00:00Z",
  confirmed_by_telecaller_id: "tc-1",
  confirmed_by_telecaller_name: "Sourabh",
  shipment_id: null,
  shipment_status: null,
  shopify_sync_status: null,
  shiprocket_order_id: "1576398335",
  awb: null,
  courier_name: null,
}

type ProcessOpts = {
  onSuccess?: (r: {
    processed_count: number
    skipped_count: number
    failed_count: number
    results: ProcessExistingShipmentResult[]
  }) => void
  onError?: (e: unknown) => void
}

function mockShipHooks(
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
  mockedUseRetryShopifySync.mockReturnValue({
    mutate: vi.fn(),
    isPending: false,
  } as unknown as ReturnType<typeof useRetryShopifySync>)
}

describe("ShipmentQueuePage (legacy alias for /fulfillment/orders)", () => {
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
  })

  afterEach(() => {
    openSpy.mockRestore()
    vi.clearAllMocks()
  })

  it("renders orders needing shipment; Ship Order processes it and opens the plain Shiprocket Ready to Ship page from the result dialog", async () => {
    const user = userEvent.setup()
    mockedUseShipmentQueue.mockReturnValue({
      isLoading: false,
      isError: false,
      error: null,
      data: {
        data: [ROW],
        meta: { page: 1, page_size: 20, total_items: 1, total_pages: 1 },
      },
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useShipmentQueue>)
    mockedUseTeamTelecallers.mockReturnValue({
      data: [{ telecaller_id: "tc-1", telecaller_name: "Sourabh" }],
    } as unknown as ReturnType<typeof useTeamTelecallers>)
    mockShipHooks((orderIds, opts) =>
      opts?.onSuccess?.({
        processed_count: 1,
        skipped_count: 0,
        failed_count: 0,
        results: orderIds.map((id) => ({
          order_id: id,
          order_number: "OMS-0001",
          status: "success",
          shiprocket_shipment_id: "7001",
          shiprocket_order_id: "1900007001",
          awb: "AWB90001",
          courier_name: "Delhivery",
          reason: null,
        })),
      })
    )

    renderWithProviders(<ShipmentQueuePage />)

    expect(screen.getByText("Alice")).toBeInTheDocument()
    expect(screen.getByText("OMS-0001")).toBeInTheDocument()
    expect(screen.getByText("Ready to Ship")).toBeInTheDocument()

    await user.click(screen.getByRole("button", { name: /^Ship Order$/i }))
    const dialog = await screen.findByRole("dialog")
    expect(within(dialog).getByText(/Courier: Delhivery/i)).toBeInTheDocument()

    await user.click(
      within(dialog).getByRole("button", { name: /^Open Shiprocket Ready to Ship$/i })
    )
    expect(openSpy).toHaveBeenCalledWith(READY_TO_SHIP_URL, "_blank", "noopener,noreferrer")
    expect(clipboardSpy).not.toHaveBeenCalled()
  })

  it("shows the failure reason, never a Shiprocket create-shipment API call, when no shipment can be resolved", async () => {
    const user = userEvent.setup()
    mockedUseShipmentQueue.mockReturnValue({
      isLoading: false,
      isError: false,
      error: null,
      data: {
        data: [{ ...ROW, shiprocket_order_id: null }],
        meta: { page: 1, page_size: 20, total_items: 1, total_pages: 1 },
      },
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useShipmentQueue>)
    mockedUseTeamTelecallers.mockReturnValue({
      data: [{ telecaller_id: "tc-1", telecaller_name: "Sourabh" }],
    } as unknown as ReturnType<typeof useTeamTelecallers>)
    mockShipHooks()

    renderWithProviders(<ShipmentQueuePage />)

    await user.click(screen.getByRole("button", { name: /^Ship Order$/i }))
    const dialog = await screen.findByRole("dialog")
    expect(
      within(dialog).getByText(/Existing Shiprocket order could not be located/i)
    ).toBeInTheDocument()
  })

  it("shows an empty state when there are no confirmed orders awaiting shipment", () => {
    mockedUseShipmentQueue.mockReturnValue({
      isLoading: false,
      isError: false,
      error: null,
      data: { data: [], meta: { page: 1, page_size: 20, total_items: 0, total_pages: 0 } },
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useShipmentQueue>)
    mockedUseTeamTelecallers.mockReturnValue({
      data: [],
    } as unknown as ReturnType<typeof useTeamTelecallers>)
    mockShipHooks()

    renderWithProviders(<ShipmentQueuePage />)

    expect(screen.getByText("No confirmed orders awaiting shipment")).toBeInTheDocument()
  })
})

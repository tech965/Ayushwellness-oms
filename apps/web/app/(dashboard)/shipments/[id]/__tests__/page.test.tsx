import { describe, expect, it, vi } from "vitest"
import { screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { renderWithProviders } from "@/test-utils/render-with-providers"
import ShipmentDetailPage from "@/app/(dashboard)/shipments/[id]/page"
import { useAuth } from "@/lib/auth-context"
import {
  useAssignAwb,
  useCancelShipment,
  useRefreshTracking,
  useRequestPickup,
  useRetryShopifySync,
  useShipment,
  useShipmentTimeline,
} from "@/services/shipments"

vi.mock("next/navigation", () => ({
  useParams: () => ({ id: "shipment-1" }),
}))

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn() } }))

vi.mock("@/lib/auth-context", () => ({ useAuth: vi.fn() }))

vi.mock("@/services/shipments", () => ({
  useShipment: vi.fn(),
  useShipmentTimeline: vi.fn(),
  useAssignAwb: vi.fn(),
  useCancelShipment: vi.fn(),
  useRequestPickup: vi.fn(),
  useRefreshTracking: vi.fn(),
  useRetryShopifySync: vi.fn(),
}))

const mockedUseAuth = vi.mocked(useAuth)
const mockedUseShipment = vi.mocked(useShipment)
const mockedUseShipmentTimeline = vi.mocked(useShipmentTimeline)
const mockedUseAssignAwb = vi.mocked(useAssignAwb)
const mockedUseCancelShipment = vi.mocked(useCancelShipment)
const mockedUseRequestPickup = vi.mocked(useRequestPickup)
const mockedUseRefreshTracking = vi.mocked(useRefreshTracking)
const mockedUseRetryShopifySync = vi.mocked(useRetryShopifySync)

const BASE_SHIPMENT = {
  id: "shipment-1",
  order_id: "order-1",
  shiprocket_shipment_id: "5001",
  awb: "AWB999",
  courier_id: "courier-1",
  current_status: "picked_up",
  delay_status: "on_time",
  ndr_status: null,
  rto_status: null,
  pickup_date: null,
  expected_delivery_date: null,
  actual_delivery_date: null,
  current_location: null,
  last_tracking_update_at: null,
  source_system: "shiprocket",
  shopify_fulfillment_id: null,
  shopify_sync_status: "pending",
  shopify_sync_error: null,
  shopify_synced_at: null,
  created_at: "2026-09-01T00:00:00Z",
  updated_at: "2026-09-01T00:00:00Z",
}

function mockCommonHooks() {
  mockedUseShipmentTimeline.mockReturnValue({
    isLoading: false,
    isError: false,
    error: null,
    data: [],
    refetch: vi.fn(),
  } as unknown as ReturnType<typeof useShipmentTimeline>)
  mockedUseAssignAwb.mockReturnValue({
    mutate: vi.fn(),
    isPending: false,
  } as unknown as ReturnType<typeof useAssignAwb>)
  mockedUseCancelShipment.mockReturnValue({
    mutate: vi.fn(),
    isPending: false,
  } as unknown as ReturnType<typeof useCancelShipment>)
  mockedUseRequestPickup.mockReturnValue({
    mutate: vi.fn(),
    isPending: false,
  } as unknown as ReturnType<typeof useRequestPickup>)
  mockedUseRefreshTracking.mockReturnValue({
    mutate: vi.fn(),
    isPending: false,
  } as unknown as ReturnType<typeof useRefreshTracking>)
}

describe("ShipmentDetailPage — Shopify sync", () => {
  it("shows a Failed badge, the error message, and a Retry button when sync failed", async () => {
    const user = userEvent.setup()
    const retryMutate = vi.fn((_vars, opts) =>
      opts.onSuccess({ ...BASE_SHIPMENT, shopify_sync_status: "synced" })
    )
    mockedUseAuth.mockReturnValue({
      hasPermission: () => true,
    } as unknown as ReturnType<typeof useAuth>)
    mockedUseShipment.mockReturnValue({
      isLoading: false,
      isError: false,
      error: null,
      data: {
        ...BASE_SHIPMENT,
        shopify_sync_status: "failed",
        shopify_sync_error: "Order is already fulfilled.",
      },
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useShipment>)
    mockCommonHooks()
    mockedUseRetryShopifySync.mockReturnValue({
      mutate: retryMutate,
      isPending: false,
    } as unknown as ReturnType<typeof useRetryShopifySync>)

    renderWithProviders(<ShipmentDetailPage />)

    expect(screen.getByText("Failed")).toBeInTheDocument()
    expect(
      screen.getByText("Shopify sync failed: Order is already fulfilled.")
    ).toBeInTheDocument()

    await user.click(screen.getByRole("button", { name: /Retry Shopify Sync/i }))
    expect(retryMutate).toHaveBeenCalled()
  })

  it("hides the Retry button once sync has succeeded", () => {
    mockedUseAuth.mockReturnValue({
      hasPermission: () => true,
    } as unknown as ReturnType<typeof useAuth>)
    mockedUseShipment.mockReturnValue({
      isLoading: false,
      isError: false,
      error: null,
      data: {
        ...BASE_SHIPMENT,
        shopify_sync_status: "synced",
        shopify_fulfillment_id: "gid://shopify/Fulfillment/1",
      },
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useShipment>)
    mockCommonHooks()
    mockedUseRetryShopifySync.mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useRetryShopifySync>)

    renderWithProviders(<ShipmentDetailPage />)

    expect(screen.getByText("Synced")).toBeInTheDocument()
    expect(
      screen.queryByRole("button", { name: /Retry Shopify Sync/i })
    ).not.toBeInTheDocument()
  })

  it("never shows the Retry button to a viewer without shipments.update", () => {
    mockedUseAuth.mockReturnValue({
      hasPermission: () => false,
    } as unknown as ReturnType<typeof useAuth>)
    mockedUseShipment.mockReturnValue({
      isLoading: false,
      isError: false,
      error: null,
      data: {
        ...BASE_SHIPMENT,
        shopify_sync_status: "failed",
        shopify_sync_error: "Order is already fulfilled.",
      },
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useShipment>)
    mockCommonHooks()
    mockedUseRetryShopifySync.mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useRetryShopifySync>)

    renderWithProviders(<ShipmentDetailPage />)

    expect(
      screen.queryByRole("button", { name: /Retry Shopify Sync/i })
    ).not.toBeInTheDocument()
  })
})

describe("ShipmentDetailPage — Cancel Shipment", () => {
  it("shows a confirmation dialog before cancelling a not-yet-picked-up shipment", async () => {
    const user = userEvent.setup()
    const cancelMutate = vi.fn((_vars, opts) => opts.onSuccess())
    mockedUseAuth.mockReturnValue({
      hasPermission: () => true,
    } as unknown as ReturnType<typeof useAuth>)
    mockedUseShipment.mockReturnValue({
      isLoading: false,
      isError: false,
      error: null,
      data: { ...BASE_SHIPMENT, current_status: "pending" },
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useShipment>)
    mockCommonHooks()
    mockedUseCancelShipment.mockReturnValue({
      mutate: cancelMutate,
      isPending: false,
    } as unknown as ReturnType<typeof useCancelShipment>)
    mockedUseRetryShopifySync.mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useRetryShopifySync>)

    renderWithProviders(<ShipmentDetailPage />)

    await user.click(screen.getByRole("button", { name: /^Cancel Shipment$/i }))
    // The mutation must not fire just from opening the dialog -- it's a
    // confirmation, not an immediate action.
    expect(cancelMutate).not.toHaveBeenCalled()
    expect(
      screen.getByText("Are you sure you want to cancel this shipment?")
    ).toBeInTheDocument()

    const dialog = screen.getByRole("alertdialog")
    await user.click(within(dialog).getByRole("button", { name: /^Cancel Shipment$/i }))
    expect(cancelMutate).toHaveBeenCalled()
  })

  it("never offers Cancel Shipment once picked up -- shows the cannot-be-reversed message instead", () => {
    mockedUseAuth.mockReturnValue({
      hasPermission: () => true,
    } as unknown as ReturnType<typeof useAuth>)
    mockedUseShipment.mockReturnValue({
      isLoading: false,
      isError: false,
      error: null,
      data: { ...BASE_SHIPMENT, current_status: "picked_up" },
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useShipment>)
    mockCommonHooks()
    mockedUseRetryShopifySync.mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useRetryShopifySync>)

    renderWithProviders(<ShipmentDetailPage />)

    expect(screen.queryByRole("button", { name: /Cancel Shipment/i })).not.toBeInTheDocument()
    expect(
      screen.getByText("Shipment cannot be automatically reversed at this stage. Contact Admin.")
    ).toBeInTheDocument()
  })

  it("never offers Cancel Shipment (or the contact-admin message) once already cancelled", () => {
    mockedUseAuth.mockReturnValue({
      hasPermission: () => true,
    } as unknown as ReturnType<typeof useAuth>)
    mockedUseShipment.mockReturnValue({
      isLoading: false,
      isError: false,
      error: null,
      data: { ...BASE_SHIPMENT, current_status: "cancelled" },
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useShipment>)
    mockCommonHooks()
    mockedUseRetryShopifySync.mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useRetryShopifySync>)

    renderWithProviders(<ShipmentDetailPage />)

    expect(screen.queryByRole("button", { name: /Cancel Shipment/i })).not.toBeInTheDocument()
    expect(
      screen.queryByText("Shipment cannot be automatically reversed at this stage. Contact Admin.")
    ).not.toBeInTheDocument()
  })

  it("delivered shipments also show the cannot-be-reversed message, never a false Undo", () => {
    mockedUseAuth.mockReturnValue({
      hasPermission: () => true,
    } as unknown as ReturnType<typeof useAuth>)
    mockedUseShipment.mockReturnValue({
      isLoading: false,
      isError: false,
      error: null,
      data: { ...BASE_SHIPMENT, current_status: "delivered" },
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useShipment>)
    mockCommonHooks()
    mockedUseRetryShopifySync.mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useRetryShopifySync>)

    renderWithProviders(<ShipmentDetailPage />)

    expect(screen.queryByRole("button", { name: /Cancel Shipment/i })).not.toBeInTheDocument()
    expect(
      screen.getByText("Shipment cannot be automatically reversed at this stage. Contact Admin.")
    ).toBeInTheDocument()
  })
})

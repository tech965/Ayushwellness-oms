import { describe, expect, it, vi } from "vitest"
import { screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { renderWithProviders } from "@/test-utils/render-with-providers"
import { SyncActions } from "@/app/(dashboard)/integrations/[id]/page"
import { useTriggerSync } from "@/services/integrations"

vi.mock("@/services/integrations", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/services/integrations")>()
  return { ...actual, useTriggerSync: vi.fn() }
})

const mockedUseTriggerSync = vi.mocked(useTriggerSync)

function mutationResult(mutateAsync: ReturnType<typeof vi.fn>) {
  return {
    mutate: vi.fn(),
    mutateAsync,
    isPending: false,
  } as unknown as ReturnType<typeof useTriggerSync>
}

describe("SyncActions", () => {
  it("lists Sync Shipments alongside Sync Tracking and Sync NDR for Shiprocket", () => {
    mockedUseTriggerSync.mockReturnValue(mutationResult(vi.fn().mockResolvedValue(undefined)))

    renderWithProviders(<SyncActions integrationId="int-1" code="shiprocket" />)

    expect(screen.getByRole("button", { name: "Sync Shipments" })).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Sync Tracking" })).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Sync NDR" })).toBeInTheDocument()
  })

  it("queues shipments, tracking, and ndr when Full Sync is clicked", async () => {
    const mutateAsync = vi.fn().mockResolvedValue(undefined)
    mockedUseTriggerSync.mockReturnValue(mutationResult(mutateAsync))
    const user = userEvent.setup()

    renderWithProviders(<SyncActions integrationId="int-1" code="shiprocket" />)
    await user.click(screen.getByRole("button", { name: "Full Sync" }))

    expect(mutateAsync).toHaveBeenCalledWith({ entityType: "shipments", syncType: "full" })
    expect(mutateAsync).toHaveBeenCalledWith({ entityType: "tracking", syncType: "full" })
    expect(mutateAsync).toHaveBeenCalledWith({ entityType: "ndr", syncType: "full" })
    expect(mutateAsync).toHaveBeenCalledTimes(3)
  })

  it("still queues the remaining entities when one entity fails to queue", async () => {
    // Regression test: a rejected trigger for one entity (e.g. a sync
    // already active for it) must never stop the loop from attempting
    // the rest -- matches the backend's own "one entity's failure never
    // blocks another entity's sync" guarantee.
    const mutateAsync = vi
      .fn()
      .mockImplementationOnce(() => Promise.reject(new Error("A shipments sync is already running.")))
      .mockResolvedValue(undefined)
    mockedUseTriggerSync.mockReturnValue(mutationResult(mutateAsync))
    const user = userEvent.setup()

    renderWithProviders(<SyncActions integrationId="int-1" code="shiprocket" />)
    await user.click(screen.getByRole("button", { name: "Full Sync" }))

    expect(mutateAsync).toHaveBeenCalledTimes(3)
    expect(mutateAsync).toHaveBeenNthCalledWith(1, { entityType: "shipments", syncType: "full" })
    expect(mutateAsync).toHaveBeenNthCalledWith(2, { entityType: "tracking", syncType: "full" })
    expect(mutateAsync).toHaveBeenNthCalledWith(3, { entityType: "ndr", syncType: "full" })
  })

  it("lists all three Shopify entities for Full Sync", async () => {
    const mutateAsync = vi.fn().mockResolvedValue(undefined)
    mockedUseTriggerSync.mockReturnValue(mutationResult(mutateAsync))
    const user = userEvent.setup()

    renderWithProviders(<SyncActions integrationId="int-1" code="shopify" />)
    await user.click(screen.getByRole("button", { name: "Full Sync" }))

    expect(mutateAsync).toHaveBeenCalledWith({ entityType: "customers", syncType: "full" })
    expect(mutateAsync).toHaveBeenCalledWith({ entityType: "products", syncType: "full" })
    expect(mutateAsync).toHaveBeenCalledWith({ entityType: "orders", syncType: "full" })
    expect(mutateAsync).toHaveBeenCalledTimes(3)
  })
})

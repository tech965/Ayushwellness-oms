import { beforeEach, describe, expect, it, vi } from "vitest"
import { screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { toast } from "sonner"

import { CashfreeSyncPanel } from "@/components/remittance/cashfree-sync-panel"
import { renderWithProviders } from "@/test-utils/render-with-providers"
import { useCashfreeSyncStatus } from "@/services/remittance"
import type { CashfreeSyncStatus } from "@/types/remittance"

const syncMutate = vi.fn()
vi.mock("@/services/remittance", () => ({
  useCashfreeSyncStatus: vi.fn(),
  useCashfreeSyncNow: () => ({ mutateAsync: syncMutate, isPending: false }),
}))
vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
}))

const NOTE =
  "A Cashfree settlement marked SUCCESS is not proof the money reached the bank."

function status(over: Partial<CashfreeSyncStatus> = {}): CashfreeSyncStatus {
  return {
    gateway: "Cashfree",
    connection_status: "connected",
    connection_message: "Last sync completed successfully.",
    configured: true,
    environment: "production",
    sync_enabled: true,
    sync_interval_minutes: 60,
    last_attempt_at: "2026-11-24T06:00:00Z",
    last_successful_sync_at: "2026-11-24T06:00:00Z",
    last_sync_status: "ok",
    last_sync_error: null,
    last_sync_trigger: "scheduled",
    synced_through: "2026-11-24T06:00:00Z",
    last_webhook_at: null,
    running: false,
    settlement_lots: 3,
    settlement_lots_by_status: { SUCCESS: 2, PENDING: 1 },
    synced_lines: 12,
    bank_confirmation_note: NOTE,
    ...over,
  }
}

const given = (data: CashfreeSyncStatus | undefined, isError = false) =>
  vi.mocked(useCashfreeSyncStatus).mockReturnValue({ data, isError } as never)

describe("CashfreeSyncPanel", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    syncMutate.mockResolvedValue({ status: "queued" })
  })

  it("shows connected, the last successful sync time, lot counts and the bank-credit caveat", () => {
    given(status())
    renderWithProviders(<CashfreeSyncPanel canSync />)
    expect(screen.getByTestId("cashfree-connection")).toHaveTextContent("Connected")
    expect(screen.getByTestId("cashfree-last-sync")).toHaveTextContent(
      /Last successful sync:\s*(?!never)\S+/
    )
    expect(screen.getByTestId("cashfree-last-sync")).toHaveTextContent("every 60 min")
    expect(
      screen.getByText(/3 settlement lots \(2 success · 1 pending\)/)
    ).toBeInTheDocument()
    expect(screen.getByText(/12 synced transaction lines/)).toBeInTheDocument()
    expect(screen.getByText(NOTE)).toBeInTheDocument()
  })

  it("says Not Configured, shows no sync data and disables Sync Now when credentials are missing", () => {
    given(
      status({
        connection_status: "not_configured",
        connection_message: "Cashfree credentials are not configured on the server.",
        configured: false,
        last_attempt_at: null,
        last_successful_sync_at: null,
        settlement_lots: 0,
        settlement_lots_by_status: {},
        synced_lines: 0,
      })
    )
    renderWithProviders(<CashfreeSyncPanel canSync />)
    expect(screen.getByTestId("cashfree-connection")).toHaveTextContent("Not Configured")
    expect(screen.getByText(/No Cashfree data is shown/)).toBeInTheDocument()
    expect(screen.queryByTestId("cashfree-last-sync")).not.toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Sync Now" })).toBeDisabled()
  })

  it("starts a sync from Sync Now and shows it as running", async () => {
    given(status())
    const user = userEvent.setup()
    renderWithProviders(<CashfreeSyncPanel canSync />)
    await user.click(screen.getByRole("button", { name: "Sync Now" }))
    expect(syncMutate).toHaveBeenCalledTimes(1)
    expect(await screen.findByRole("button", { name: "Syncing…" })).toBeDisabled()
  })

  it("hides Sync Now from users who cannot sync but still shows the status", () => {
    given(status())
    renderWithProviders(<CashfreeSyncPanel canSync={false} />)
    expect(screen.queryByRole("button", { name: /Sync Now/ })).not.toBeInTheDocument()
    expect(screen.getByTestId("cashfree-connection")).toHaveTextContent("Connected")
  })

  it("surfaces a partial or failed last attempt with its error", () => {
    given(
      status({
        connection_status: "degraded",
        last_sync_status: "partial",
        last_sync_error: "settlement_lines: Cashfree API returned a server error (500).",
        last_successful_sync_at: null,
      })
    )
    renderWithProviders(<CashfreeSyncPanel canSync />)
    expect(screen.getByTestId("cashfree-connection")).toHaveTextContent(
      "Partially synced"
    )
    expect(screen.getByRole("alert")).toHaveTextContent("server error (500)")
    expect(screen.getByTestId("cashfree-last-sync")).toHaveTextContent("never")
  })

  it("never renders anything credential-like and survives a status error", () => {
    given(undefined, true)
    renderWithProviders(<CashfreeSyncPanel canSync />)
    expect(screen.getByText("Cashfree sync status is unavailable.")).toBeInTheDocument()
  })

  it("shows a queued sync as waiting for the worker and blocks a second click", () => {
    given(status({ queued: true, last_sync_status: "queued" }))
    renderWithProviders(<CashfreeSyncPanel canSync />)
    expect(screen.getByRole("button", { name: "Queued…" })).toBeDisabled()
    expect(screen.getByRole("status")).toHaveTextContent(
      "Waiting for the background worker"
    )
  })

  it.each(["queued", "already_queued", "already_running"])(
    "keeps watching after a %s response",
    async (outcome) => {
      syncMutate.mockResolvedValue({ status: outcome, message: "ok" })
      given(status())
      const user = userEvent.setup()
      renderWithProviders(<CashfreeSyncPanel canSync />)
      await user.click(screen.getByRole("button", { name: "Sync Now" }))
      expect(await screen.findByRole("button", { name: "Syncing…" })).toBeDisabled()
      expect(vi.mocked(useCashfreeSyncStatus).mock.calls.at(-1)![0]).toBe(
        "2026-11-24T06:00:00Z"
      )
    }
  )

  it("shows why a sync could not be queued and stays clickable", async () => {
    syncMutate.mockRejectedValue(new Error("The sync could not be queued"))
    given(status())
    const user = userEvent.setup()
    renderWithProviders(<CashfreeSyncPanel canSync />)
    await user.click(screen.getByRole("button", { name: "Sync Now" }))
    expect(toast.error).toHaveBeenCalled()
    expect(screen.getByRole("button", { name: "Sync Now" })).toBeEnabled()
  })
})

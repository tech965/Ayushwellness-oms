import { describe, expect, it, vi } from "vitest"
import { screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { renderWithProviders } from "@/test-utils/render-with-providers"
import { CashfreeSyncTransactionsButton } from "@/components/payments/cashfree-sync-transactions-button"
import { useSyncCashfreeTransactions } from "@/services/cashfree"
import { useAuth } from "@/lib/auth-context"

vi.mock("@/services/cashfree", () => ({
  useSyncCashfreeTransactions: vi.fn(),
}))

vi.mock("@/lib/auth-context", () => ({
  useAuth: vi.fn(),
}))

const mockedUseSync = vi.mocked(useSyncCashfreeTransactions)
const mockedUseAuth = vi.mocked(useAuth)

function mutationResult(overrides: Record<string, unknown> = {}) {
  return {
    mutate: vi.fn(),
    isPending: false,
    ...overrides,
  } as unknown as ReturnType<typeof useSyncCashfreeTransactions>
}

function setAuth(canSync: boolean) {
  mockedUseAuth.mockReturnValue({
    hasPermission: (code: string) => (code === "payments.create" ? canSync : true),
  } as unknown as ReturnType<typeof useAuth>)
}

describe("CashfreeSyncTransactionsButton", () => {
  it("is hidden for a user without payments.create", () => {
    setAuth(false)
    mockedUseSync.mockReturnValue(mutationResult())
    renderWithProviders(
      <CashfreeSyncTransactionsButton dateFrom="2026-09-03T00:00:00Z" dateTo="2026-09-03T23:59:59Z" />
    )
    expect(screen.queryByRole("button")).not.toBeInTheDocument()
  })

  it("calls the sync mutation with the given date range on click", async () => {
    setAuth(true)
    const mutation = mutationResult()
    mockedUseSync.mockReturnValue(mutation)
    renderWithProviders(
      <CashfreeSyncTransactionsButton dateFrom="2026-09-03T00:00:00Z" dateTo="2026-09-03T23:59:59Z" />
    )

    await userEvent.click(screen.getByRole("button", { name: "Sync Cashfree Transactions" }))

    expect(mutation.mutate).toHaveBeenCalledTimes(1)
    expect(mutation.mutate).toHaveBeenCalledWith(
      { date_from: "2026-09-03T00:00:00Z", date_to: "2026-09-03T23:59:59Z" },
      expect.anything()
    )
  })

  it("shows a loading state while the sync is pending", () => {
    setAuth(true)
    mockedUseSync.mockReturnValue(mutationResult({ isPending: true }))
    renderWithProviders(
      <CashfreeSyncTransactionsButton dateFrom="2026-09-03T00:00:00Z" dateTo="2026-09-03T23:59:59Z" />
    )
    expect(screen.getByRole("button", { name: "Syncing..." })).toBeDisabled()
  })
})

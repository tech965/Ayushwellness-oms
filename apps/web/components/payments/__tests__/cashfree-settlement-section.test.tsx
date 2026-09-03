import { describe, expect, it, vi } from "vitest"
import { screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { renderWithProviders } from "@/test-utils/render-with-providers"
import { CashfreeSettlementSection } from "@/components/payments/cashfree-settlement-section"
import { useCashfreeSettlementSummary, useSyncCashfreeSettlements } from "@/services/cashfree"
import { useAuth } from "@/lib/auth-context"
import type { CashfreeSettlementSummary } from "@/types/cashfree"

vi.mock("@/services/cashfree", () => ({
  useCashfreeSettlementSummary: vi.fn(),
  useSyncCashfreeSettlements: vi.fn(),
}))

vi.mock("@/lib/auth-context", () => ({
  useAuth: vi.fn(),
}))

const mockedUseSummary = vi.mocked(useCashfreeSettlementSummary)
const mockedUseSync = vi.mocked(useSyncCashfreeSettlements)
const mockedUseAuth = vi.mocked(useAuth)

function queryResult(overrides: Record<string, unknown> = {}) {
  return {
    isLoading: false,
    isError: false,
    error: null,
    data: undefined,
    refetch: vi.fn(),
    ...overrides,
  } as unknown as ReturnType<typeof useCashfreeSettlementSummary>
}

function mutationResult(overrides: Record<string, unknown> = {}) {
  return {
    mutate: vi.fn(),
    isPending: false,
    ...overrides,
  } as unknown as ReturnType<typeof useSyncCashfreeSettlements>
}

function setAuth(canSync: boolean) {
  mockedUseAuth.mockReturnValue({
    hasPermission: (code: string) => (code === "payments.create" ? canSync : true),
  } as unknown as ReturnType<typeof useAuth>)
}

const summary: CashfreeSettlementSummary = {
  unsettled_amount: "1250.00",
  upcoming_settlement_amount: "900.00",
  upcoming_settlement_status: "PENDING",
  last_settled_amount: "5000.00",
  last_settled_date: "2026-09-01T10:00:00Z",
  last_settlement_utr: "UTR123456",
  last_settlement_status: "SUCCESS",
  history: [
    {
      cf_settlement_id: "stl_1",
      status: "SUCCESS",
      settlement_utr: "UTR123456",
      settlement_processed_on: "2026-09-01T10:00:00Z",
      payment_amount: "5100.00",
      amount_settled: "5000.00",
    },
  ],
}

describe("CashfreeSettlementSection", () => {
  it("shows a loading skeleton while the summary is loading", () => {
    setAuth(true)
    mockedUseSummary.mockReturnValue(queryResult({ isLoading: true }))
    mockedUseSync.mockReturnValue(mutationResult())
    const { container } = renderWithProviders(
      <CashfreeSettlementSection dateFrom="2026-09-01T00:00:00Z" dateTo="2026-09-03T23:59:59Z" />
    )
    expect(container.querySelectorAll('[class*="animate-pulse"]').length).toBeGreaterThan(0)
  })

  it("renders settlement tiles and history rows from real data", () => {
    setAuth(true)
    mockedUseSummary.mockReturnValue(queryResult({ data: summary }))
    mockedUseSync.mockReturnValue(mutationResult())
    renderWithProviders(
      <CashfreeSettlementSection dateFrom="2026-09-01T00:00:00Z" dateTo="2026-09-03T23:59:59Z" />
    )

    expect(screen.getByText("Unsettled Amount")).toBeInTheDocument()
    expect(screen.getByText("Upcoming Settlement")).toBeInTheDocument()
    expect(screen.getByText("Last Settled")).toBeInTheDocument()
    expect(screen.getAllByText("UTR123456").length).toBeGreaterThan(0)
    expect(screen.getAllByText("SUCCESS").length).toBeGreaterThan(0)
  })

  it("shows an empty-history message when no settlements have been synced yet", () => {
    setAuth(true)
    mockedUseSummary.mockReturnValue(
      queryResult({ data: { ...summary, history: [] } })
    )
    mockedUseSync.mockReturnValue(mutationResult())
    renderWithProviders(
      <CashfreeSettlementSection dateFrom="2026-09-01T00:00:00Z" dateTo="2026-09-03T23:59:59Z" />
    )
    expect(screen.getByText(/No settlements found/)).toBeInTheDocument()
  })

  it("shows an error banner with retry instead of fabricating zero settlements", async () => {
    setAuth(true)
    const refetch = vi.fn()
    mockedUseSummary.mockReturnValue(
      queryResult({ isError: true, error: new Error("Network error"), refetch })
    )
    mockedUseSync.mockReturnValue(mutationResult())
    renderWithProviders(
      <CashfreeSettlementSection dateFrom="2026-09-01T00:00:00Z" dateTo="2026-09-03T23:59:59Z" />
    )

    expect(screen.getByText("Unable to load Cashfree settlement data")).toBeInTheDocument()
    expect(screen.queryByText("Unsettled Amount")).not.toBeInTheDocument()

    await userEvent.click(screen.getByRole("button", { name: "Retry" }))
    expect(refetch).toHaveBeenCalledTimes(1)
  })

  it("calls the settlement sync mutation with the given date range on click", async () => {
    setAuth(true)
    mockedUseSummary.mockReturnValue(queryResult({ data: summary }))
    const mutation = mutationResult()
    mockedUseSync.mockReturnValue(mutation)
    renderWithProviders(
      <CashfreeSettlementSection dateFrom="2026-09-01T00:00:00Z" dateTo="2026-09-03T23:59:59Z" />
    )

    await userEvent.click(screen.getByRole("button", { name: "Sync Settlements" }))

    expect(mutation.mutate).toHaveBeenCalledTimes(1)
    expect(mutation.mutate).toHaveBeenCalledWith(
      { date_from: "2026-09-01T00:00:00Z", date_to: "2026-09-03T23:59:59Z" },
      expect.anything()
    )
  })

  it("hides the Sync Settlements button for a user without payments.create", () => {
    setAuth(false)
    mockedUseSummary.mockReturnValue(queryResult({ data: summary }))
    mockedUseSync.mockReturnValue(mutationResult())
    renderWithProviders(
      <CashfreeSettlementSection dateFrom="2026-09-01T00:00:00Z" dateTo="2026-09-03T23:59:59Z" />
    )
    expect(screen.queryByRole("button", { name: "Sync Settlements" })).not.toBeInTheDocument()
  })
})

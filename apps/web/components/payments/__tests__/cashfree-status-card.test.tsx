import { describe, expect, it, vi } from "vitest"
import { screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { renderWithProviders } from "@/test-utils/render-with-providers"
import { CashfreeStatusCard } from "@/components/payments/cashfree-status-card"
import { useCashfreeStatus, useTestCashfreeConnection } from "@/services/cashfree"
import { useAuth } from "@/lib/auth-context"

vi.mock("@/services/cashfree", () => ({
  useCashfreeStatus: vi.fn(),
  useTestCashfreeConnection: vi.fn(),
}))

vi.mock("@/lib/auth-context", () => ({
  useAuth: vi.fn(),
}))

const mockedUseStatus = vi.mocked(useCashfreeStatus)
const mockedUseTestConnection = vi.mocked(useTestCashfreeConnection)
const mockedUseAuth = vi.mocked(useAuth)

function queryResult(overrides: Record<string, unknown> = {}) {
  return {
    isLoading: false,
    isError: false,
    error: null,
    data: undefined,
    ...overrides,
  } as unknown as ReturnType<typeof useCashfreeStatus>
}

function mutationResult(overrides: Record<string, unknown> = {}) {
  return {
    mutate: vi.fn(),
    isPending: false,
    data: undefined,
    ...overrides,
  } as unknown as ReturnType<typeof useTestCashfreeConnection>
}

function setAuth(canTest: boolean) {
  mockedUseAuth.mockReturnValue({
    hasPermission: (code: string) => (code === "integrations.test" ? canTest : true),
  } as unknown as ReturnType<typeof useAuth>)
}

describe("CashfreeStatusCard", () => {
  it("shows configured/sandbox details with no secret anywhere in the DOM", () => {
    setAuth(true)
    mockedUseStatus.mockReturnValue(
      queryResult({
        data: {
          configured: true,
          environment: "sandbox",
          api_url: "https://sandbox.cashfree.com/pg",
          api_version: "2025-01-01",
        },
      })
    )
    mockedUseTestConnection.mockReturnValue(mutationResult())

    const { container } = renderWithProviders(<CashfreeStatusCard />)

    expect(screen.getByText("Sandbox")).toBeInTheDocument()
    expect(screen.getByText("https://sandbox.cashfree.com/pg")).toBeInTheDocument()
    expect(screen.getByText("Not Tested")).toBeInTheDocument()
    expect(container.textContent).not.toMatch(/secret/i)
    expect(container.textContent).not.toMatch(/client-secret|clientsecret/i)
  })

  it("shows Not Configured and disables the test button when unconfigured", () => {
    setAuth(true)
    mockedUseStatus.mockReturnValue(
      queryResult({
        data: {
          configured: false,
          environment: "not_configured",
          api_url: null,
          api_version: null,
        },
      })
    )
    mockedUseTestConnection.mockReturnValue(mutationResult())

    renderWithProviders(<CashfreeStatusCard />)

    expect(screen.getAllByText("Not Configured").length).toBeGreaterThan(0)
    expect(screen.getByRole("button", { name: "Test Connection" })).toBeDisabled()
  })

  it("runs the test-connection mutation on click and shows a connected result", async () => {
    setAuth(true)
    mockedUseStatus.mockReturnValue(
      queryResult({
        data: {
          configured: true,
          environment: "production",
          api_url: "https://api.cashfree.com/pg",
          api_version: "2025-01-01",
        },
      })
    )
    const testMutation = mutationResult({
      data: {
        configured: true,
        connected: true,
        environment: "production",
        error_type: "not_found",
        status_code: 404,
        checked_at: "2026-02-01T10:00:00Z",
      },
    })
    mockedUseTestConnection.mockReturnValue(testMutation)

    renderWithProviders(<CashfreeStatusCard />)
    await userEvent.click(screen.getByRole("button", { name: "Test Connection" }))

    expect(testMutation.mutate).toHaveBeenCalledTimes(1)
    // Formatted with the runner's local timezone (`lib/format.ts`), so
    // only the timezone-independent prefix is asserted here.
    expect(screen.getByText(/^Reachable/)).toBeInTheDocument()
    expect(screen.getByText("Connected")).toBeInTheDocument()
  })

  it("hides the Test Connection button for a user without integrations.test", () => {
    setAuth(false)
    mockedUseStatus.mockReturnValue(
      queryResult({
        data: {
          configured: true,
          environment: "sandbox",
          api_url: "https://sandbox.cashfree.com/pg",
          api_version: "2025-01-01",
        },
      })
    )
    mockedUseTestConnection.mockReturnValue(mutationResult())

    renderWithProviders(<CashfreeStatusCard />)
    expect(screen.queryByRole("button", { name: "Test Connection" })).not.toBeInTheDocument()
  })
})

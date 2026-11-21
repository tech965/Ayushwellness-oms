import { describe, expect, it, vi } from "vitest"
import { screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { renderWithProviders } from "@/test-utils/render-with-providers"
import IntegrationDetailPage from "@/app/(dashboard)/integrations/[id]/page"
import { useAuth } from "@/lib/auth-context"
import {
  useIntegration,
  useIntegrationHealth,
  useIntegrationSyncHistory,
  useRunHealthCheck,
  useWebhookEvents,
} from "@/services/integrations"
import type { Integration, IntegrationHealth } from "@/types/integration"

/** NimbusPost's generic Integrations detail page -- proves the existing,
 * provider-agnostic page (same one Shiprocket/Shopify already use) shows
 * NimbusPost correctly with no NimbusPost-specific component, and that
 * Connected is never a hardcoded badge: status always comes from the
 * real `status`/health-check fields passed in. */

vi.mock("next/navigation", () => ({
  useParams: () => ({ id: "nimbuspost-int-1" }),
}))

vi.mock("@/lib/auth-context", () => ({
  useAuth: vi.fn(),
}))

vi.mock("@/services/integrations", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/services/integrations")>()
  return {
    ...actual,
    useIntegration: vi.fn(),
    useIntegrationHealth: vi.fn(),
    useRunHealthCheck: vi.fn(),
    useIntegrationSyncHistory: vi.fn(),
    useWebhookEvents: vi.fn(),
  }
})

const mockedUseAuth = vi.mocked(useAuth)
const mockedUseIntegration = vi.mocked(useIntegration)
const mockedUseIntegrationHealth = vi.mocked(useIntegrationHealth)
const mockedUseRunHealthCheck = vi.mocked(useRunHealthCheck)
const mockedUseIntegrationSyncHistory = vi.mocked(useIntegrationSyncHistory)
const mockedUseWebhookEvents = vi.mocked(useWebhookEvents)

const NIMBUSPOST_INTEGRATION: Integration = {
  id: "nimbuspost-int-1",
  name: "NimbusPost",
  code: "nimbuspost",
  type: "courier",
  status: "disconnected",
  enabled: false,
  configuration: null,
  last_sync_at: null,
  last_successful_sync_at: null,
  last_failure_at: "2026-11-20T10:00:00Z",
  last_failure_message: "NimbusPost integration is not configured (missing NIMBUSPOST_EMAIL/NIMBUSPOST_PASSWORD).",
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
}

function queryResult<T>(data: T | undefined, overrides: Record<string, unknown> = {}): unknown {
  return {
    data,
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
    ...overrides,
  }
}

function pagedResult() {
  return queryResult({
    success: true,
    message: "Success",
    data: [],
    meta: { page: 1, page_size: 20, total_items: 0, total_pages: 0 },
  })
}

function setUp(health: IntegrationHealth) {
  mockedUseAuth.mockReturnValue({
    hasPermission: (code: string) => code === "integrations.test",
  } as unknown as ReturnType<typeof useAuth>)
  mockedUseIntegration.mockReturnValue(
    queryResult(NIMBUSPOST_INTEGRATION) as ReturnType<typeof useIntegration>
  )
  mockedUseIntegrationHealth.mockReturnValue(
    queryResult(health) as ReturnType<typeof useIntegrationHealth>
  )
  mockedUseIntegrationSyncHistory.mockReturnValue(
    pagedResult() as ReturnType<typeof useIntegrationSyncHistory>
  )
  mockedUseWebhookEvents.mockReturnValue(pagedResult() as ReturnType<typeof useWebhookEvents>)
}

describe("IntegrationDetailPage — NimbusPost", () => {
  it("shows an honest Not Configured status and the real configuration error, never a hardcoded Connected badge", () => {
    setUp({
      connected: false,
      status: "disconnected",
      response_time_ms: null,
      last_successful_sync_at: null,
      last_failure_at: "2026-11-20T10:00:00Z",
      error_message:
        "NimbusPost integration is not configured (missing NIMBUSPOST_EMAIL/NIMBUSPOST_PASSWORD).",
    })
    mockedUseRunHealthCheck.mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useRunHealthCheck>)

    renderWithProviders(<IntegrationDetailPage />)

    expect(screen.getByText("NimbusPost")).toBeInTheDocument()
    expect(screen.getByText("Code: nimbuspost")).toBeInTheDocument()
    expect(screen.getByText("Not Configured")).toBeInTheDocument()
    expect(screen.queryByText("Connected")).not.toBeInTheDocument()
    expect(
      screen.getByText(
        "NimbusPost integration is not configured (missing NIMBUSPOST_EMAIL/NIMBUSPOST_PASSWORD)."
      )
    ).toBeInTheDocument()
  })

  it("Test Connection calls the existing generic health-check mutation (no NimbusPost-specific endpoint)", async () => {
    setUp({
      connected: false,
      status: "disconnected",
      response_time_ms: null,
      last_successful_sync_at: null,
      last_failure_at: null,
      error_message: null,
    })
    const mutate = vi.fn()
    mockedUseRunHealthCheck.mockReturnValue({
      mutate,
      isPending: false,
    } as unknown as ReturnType<typeof useRunHealthCheck>)
    const user = userEvent.setup()

    renderWithProviders(<IntegrationDetailPage />)
    await user.click(screen.getByRole("button", { name: "Test Connection" }))

    expect(mutate).toHaveBeenCalledTimes(1)
  })

  it("reflects a real Connected result once health-check succeeds -- status is never assumed from env vars alone", () => {
    setUp({
      connected: true,
      status: "connected",
      response_time_ms: 182,
      last_successful_sync_at: "2026-11-20T11:00:00Z",
      last_failure_at: null,
      error_message: null,
    })
    // `integration.status` (not just the health query) drives the badge --
    // use a connected integration record, not the "disconnected" default
    // fixture, to prove the badge follows the real backend field.
    mockedUseIntegration.mockReturnValue(
      queryResult({ ...NIMBUSPOST_INTEGRATION, status: "connected", enabled: true }) as ReturnType<
        typeof useIntegration
      >
    )
    mockedUseRunHealthCheck.mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useRunHealthCheck>)

    renderWithProviders(<IntegrationDetailPage />)

    expect(screen.getAllByText("Connected").length).toBeGreaterThan(0)
  })

  it("never renders a credential, token, or password value anywhere on the page", () => {
    setUp({
      connected: false,
      status: "disconnected",
      response_time_ms: null,
      last_successful_sync_at: null,
      last_failure_at: null,
      error_message: "NimbusPost rejected the credentials.",
    })
    mockedUseRunHealthCheck.mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useRunHealthCheck>)

    const { container } = renderWithProviders(<IntegrationDetailPage />)
    const text = container.textContent ?? ""

    for (const secret of ["NIMBUSPOST_PASSWORD=", "NIMBUSPOST_API_KEY=", "Bearer ", "tok_"]) {
      expect(text).not.toContain(secret)
    }
  })

  it("shows no Sync card for NimbusPost -- no entity types are registered for it yet", () => {
    setUp({
      connected: false,
      status: "disconnected",
      response_time_ms: null,
      last_successful_sync_at: null,
      last_failure_at: null,
      error_message: null,
    })
    mockedUseAuth.mockReturnValue({
      hasPermission: () => true, // even with every permission granted...
    } as unknown as ReturnType<typeof useAuth>)
    mockedUseRunHealthCheck.mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useRunHealthCheck>)

    renderWithProviders(<IntegrationDetailPage />)

    // ... the Sync card still doesn't render, because `SYNC_ENTITY_TYPES_BY_CODE`
    // has no "nimbuspost" entry (shipment operations aren't implemented yet).
    expect(screen.queryByText("Sync")).not.toBeInTheDocument()
  })
})

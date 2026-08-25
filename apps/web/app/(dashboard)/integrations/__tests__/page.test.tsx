import { describe, expect, it, vi } from "vitest"
import { screen } from "@testing-library/react"

import { renderWithProviders } from "@/test-utils/render-with-providers"
import IntegrationsPage from "@/app/(dashboard)/integrations/page"
import { useIntegrations } from "@/services/integrations"

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}))

vi.mock("@/services/integrations", () => ({
  useIntegrations: vi.fn(),
}))

const mockedUseIntegrations = vi.mocked(useIntegrations)

function baseQueryResult(overrides: Partial<ReturnType<typeof useIntegrations>>) {
  return {
    isLoading: false,
    isError: false,
    error: null,
    data: undefined,
    refetch: vi.fn(),
    ...overrides,
  } as unknown as ReturnType<typeof useIntegrations>
}

describe("IntegrationsPage", () => {
  it("shows skeletons while loading", () => {
    mockedUseIntegrations.mockReturnValue(baseQueryResult({ isLoading: true }))
    const { container } = renderWithProviders(<IntegrationsPage />)
    expect(container.querySelectorAll("[data-slot='skeleton']").length).toBeGreaterThan(0)
  })

  it("shows an error state with a retry action", () => {
    mockedUseIntegrations.mockReturnValue(
      baseQueryResult({ isError: true, error: new Error("Network down") })
    )
    renderWithProviders(<IntegrationsPage />)
    expect(screen.getByText("Network down")).toBeInTheDocument()
  })

  it("shows an honest empty state when nothing is seeded", () => {
    mockedUseIntegrations.mockReturnValue(
      baseQueryResult({
        data: {
          success: true,
          data: [],
          message: "Success",
          meta: { page: 1, page_size: 20, total_items: 0, total_pages: 0 },
        },
      })
    )
    renderWithProviders(<IntegrationsPage />)
    expect(screen.getByText("No integrations configured")).toBeInTheDocument()
  })

  it("renders a row per integration with an honest Not Configured status, never a fake one", () => {
    mockedUseIntegrations.mockReturnValue(
      baseQueryResult({
        data: {
          success: true,
          message: "Success",
          data: [
            {
              id: "1",
              name: "Shopify",
              code: "shopify",
              type: "ecommerce",
              status: "disconnected",
              enabled: false,
              configuration: null,
              last_sync_at: null,
              last_successful_sync_at: null,
              last_failure_at: null,
              last_failure_message: null,
              created_at: "2026-01-01T00:00:00Z",
              updated_at: "2026-01-01T00:00:00Z",
            },
          ],
          meta: { page: 1, page_size: 20, total_items: 1, total_pages: 1 },
        },
      })
    )
    renderWithProviders(<IntegrationsPage />)
    expect(screen.getByText("Shopify")).toBeInTheDocument()
    expect(screen.getByText("Not Configured")).toBeInTheDocument()
    expect(screen.getAllByText("Never").length).toBeGreaterThan(0)
  })
})

import { describe, expect, it, vi } from "vitest"
import { screen, fireEvent } from "@testing-library/react"

import { renderWithProviders } from "@/test-utils/render-with-providers"
import SettingsPage from "@/app/(dashboard)/settings/page"
import { useAuth } from "@/lib/auth-context"
import { useCouriers } from "@/services/couriers"
import { useIntegrations } from "@/services/integrations"
import { useSettings, useUpdateSettings } from "@/services/settings"
import type { AppSettingsResponse } from "@/types/settings"

vi.mock("@/lib/auth-context", () => ({
  useAuth: vi.fn(),
}))

vi.mock("@/services/settings", () => ({
  useSettings: vi.fn(),
  useUpdateSettings: vi.fn(),
}))

vi.mock("@/services/couriers", () => ({
  useCouriers: vi.fn(),
}))

vi.mock("@/services/integrations", () => ({
  useIntegrations: vi.fn(),
}))

const mockedUseAuth = vi.mocked(useAuth)
const mockedUseSettings = vi.mocked(useSettings)
const mockedUseUpdateSettings = vi.mocked(useUpdateSettings)
const mockedUseCouriers = vi.mocked(useCouriers)
const mockedUseIntegrations = vi.mocked(useIntegrations)

const SETTINGS_DATA: AppSettingsResponse = {
  settings: {
    general: {
      organization_name: "AyushWellness",
      oms_display_name: "AyushWellness OMS",
      default_timezone: "Asia/Kolkata",
      currency: "INR",
      date_format: "DD MMM YYYY",
      default_page_size: 20,
    },
    orders: {
      default_order_status: "pending",
      auto_refresh_interval_seconds: 0,
      default_sort_field: "order_datetime",
      default_sort_direction: "desc",
    },
    notifications: {
      email_order_notifications: true,
      email_shipment_notifications: true,
      email_return_refund_notifications: true,
    },
    shipping: { default_courier_id: null, tracking_refresh_interval_minutes: 60 },
    dashboard: {
      default_date_range: "last_30_days",
      default_chart_interval: "day",
      refresh_interval_seconds: 0,
    },
    security: { session_timeout_minutes: 60 },
    appearance: { table_density: "comfortable" },
  },
  updated_at: null,
  updated_by_email: null,
}

function baseMocks() {
  mockedUseAuth.mockReturnValue({
    hasPermission: () => true,
  } as unknown as ReturnType<typeof useAuth>)
  mockedUseUpdateSettings.mockReturnValue({
    mutate: vi.fn(),
    isPending: false,
  } as unknown as ReturnType<typeof useUpdateSettings>)
  mockedUseCouriers.mockReturnValue({ data: [] } as unknown as ReturnType<typeof useCouriers>)
  mockedUseIntegrations.mockReturnValue({
    data: { data: [] },
    isLoading: false,
  } as unknown as ReturnType<typeof useIntegrations>)
}

describe("SettingsPage resilience", () => {
  it("TEST: shows a skeleton (not an error) while genuinely loading", () => {
    baseMocks()
    mockedUseSettings.mockReturnValue({
      data: undefined,
      isLoading: true,
      isError: false,
      error: null,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useSettings>)

    const { container } = renderWithProviders(<SettingsPage />)

    expect(container.querySelectorAll("[data-slot='skeleton']").length).toBeGreaterThan(0)
    expect(screen.queryByText(/went wrong/i)).not.toBeInTheDocument()
  })

  it("TEST: a permanently failed request (e.g. 500) shows a Retry state, never an infinite skeleton", () => {
    baseMocks()
    const refetch = vi.fn()
    mockedUseSettings.mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      error: new Error("An unexpected error occurred. Please try again later."),
      refetch,
    } as unknown as ReturnType<typeof useSettings>)

    const { container } = renderWithProviders(<SettingsPage />)

    // The old bug: `isLoading || !data` stayed true forever once isLoading
    // settled to false with no data -- this must now show real content
    // (an error + Retry), not a permanent skeleton.
    expect(container.querySelectorAll("[data-slot='skeleton']").length).toBe(0)
    expect(screen.getByText("Something went wrong")).toBeInTheDocument()
    expect(
      screen.getByText("An unexpected error occurred. Please try again later.")
    ).toBeInTheDocument()

    const retryButton = screen.getByRole("button", { name: "Retry" })
    fireEvent.click(retryButton)
    expect(refetch).toHaveBeenCalledTimes(1)
  })

  it("TEST: never leaks a raw backend stack trace -- only the server's own sanitized message is ever shown", () => {
    baseMocks()
    // Mirrors the real shape `apiClient` throws for an HTTP error: the
    // backend's `UnhandledExceptionMiddleware` already sanitizes a raw
    // `UndefinedTableError` traceback down to a generic message before it
    // ever leaves the server (see app/middleware/error_handler.py) -- this
    // is what the frontend actually receives, never the traceback itself.
    const axiosLikeError = {
      isAxiosError: true,
      message: "Request failed with status code 500",
      response: {
        data: {
          error: { message: "An unexpected error occurred. Please try again later." },
        },
      },
    }
    mockedUseSettings.mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      error: axiosLikeError,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useSettings>)

    renderWithProviders(<SettingsPage />)

    expect(
      screen.getByText("An unexpected error occurred. Please try again later.")
    ).toBeInTheDocument()
    expect(screen.queryByText(/Traceback/)).not.toBeInTheDocument()
    expect(screen.queryByText(/UndefinedTableError/)).not.toBeInTheDocument()
    expect(screen.queryByText(/app_settings/)).not.toBeInTheDocument()
  })

  it("TEST: renders the real Settings form once data loads successfully", () => {
    baseMocks()
    mockedUseSettings.mockReturnValue({
      data: SETTINGS_DATA,
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useSettings>)

    renderWithProviders(<SettingsPage />)

    expect(screen.getByLabelText("Organization name")).toHaveValue("AyushWellness")
  })
})

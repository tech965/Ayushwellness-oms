import { describe, expect, it, vi } from "vitest"
import { render, screen } from "@testing-library/react"

import { SettingsProvider, useAppSettings } from "@/lib/settings-context"
import { useSettings } from "@/services/settings"

vi.mock("@/services/settings", () => ({
  useSettings: vi.fn(),
}))

const mockedUseSettings = vi.mocked(useSettings)

function Consumer() {
  const settings = useAppSettings()
  return (
    <div>
      <p>App content rendered</p>
      <p>page size: {settings?.general.default_page_size ?? "default-20"}</p>
    </div>
  )
}

describe("SettingsProvider resilience", () => {
  it("TEST: renders the rest of the app normally when the Settings API fails (e.g. 500)", () => {
    mockedUseSettings.mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      error: new Error("settings unavailable"),
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useSettings>)

    render(
      <SettingsProvider>
        <Consumer />
      </SettingsProvider>
    )

    // The rest of the OMS must never be blocked by an optional Settings
    // failure -- content renders, and every consumer falls back to its
    // own safe default rather than crashing on `undefined`.
    expect(screen.getByText("App content rendered")).toBeInTheDocument()
    expect(screen.getByText("page size: default-20")).toBeInTheDocument()
  })

  it("provides real settings once loaded successfully", () => {
    mockedUseSettings.mockReturnValue({
      data: {
        settings: { general: { default_page_size: 50 } },
        updated_at: null,
        updated_by_email: null,
      },
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    } as unknown as ReturnType<typeof useSettings>)

    render(
      <SettingsProvider>
        <Consumer />
      </SettingsProvider>
    )

    expect(screen.getByText("page size: 50")).toBeInTheDocument()
  })
})

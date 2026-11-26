import { afterEach, describe, expect, it, vi } from "vitest"
import { render, screen } from "@testing-library/react"

import { PreviewEnvironmentGuard } from "@/components/layout/preview-environment-guard"
import {
  LOCAL_DEFAULT_API_URL,
  PRODUCTION_API_HOSTS,
  resolveApiConfig,
} from "@/lib/api-config"

const PRODUCTION = "https://ayushwellness-oms.onrender.com/api/v1"
const preview = (apiUrl?: string, previewApiHost?: string) =>
  resolveApiConfig({ deployEnv: "preview", apiUrl, previewApiHost })

describe("production and local development are unchanged", () => {
  it.each(["production", "development", "", undefined])(
    "deploy env %s keeps the configured URL, or the local default",
    (deployEnv) => {
      const env = { deployEnv, previewApiHost: undefined }
      expect(resolveApiConfig({ ...env, apiUrl: PRODUCTION })).toEqual({
        isPreview: false,
        baseUrl: PRODUCTION,
        problem: null,
      })
      expect(resolveApiConfig({ ...env, apiUrl: undefined }).baseUrl).toBe(
        LOCAL_DEFAULT_API_URL
      )
    }
  )
})

describe("preview fails closed", () => {
  it.each([undefined, "", "   "])("never falls back when the URL is %j", (value) => {
    const config = preview(value)
    expect(config.baseUrl).toBeNull()
    expect(config.problem).toContain("not set")
  })

  it.each([
    PRODUCTION,
    "https://AyushWellness-OMS.onrender.com/api/v1",
    "https://ayushwellness-oms.onrender.com./api/v1",
    "https://ayushwellness-oms.onrender.com:443/api/v1",
    "http://ayushwellness-oms.onrender.com/api/v1",
    "  https://ayushwellness-oms.onrender.com/api/v1  ",
  ])("blocks the production API written as %s", (value) => {
    expect(preview(value).baseUrl).toBeNull()
    expect(preview(value).problem).toContain("PRODUCTION")
  })

  it("blocks production even if someone approves it as the preview host", () => {
    for (const host of PRODUCTION_API_HOSTS) {
      expect(preview(`https://${host}/api/v1`, host).baseUrl).toBeNull()
    }
  })

  it.each([
    "/api/v1",
    "ayushwellness-oms.onrender.com/api/v1",
    "localhost:8000/api/v1",
    "ftp://localhost/api",
    "https://localhost@ayushwellness-oms.onrender.com/api/v1",
  ])("blocks the unusable or disguised value %s", (value) => {
    expect(preview(value).baseUrl).toBeNull()
  })

  it.each([
    "https://some-other-api.example.com/api/v1",
    "https://ayushwellness-api.vercel.app/api/v1",
    "http://192.168.1.20:8000/api/v1",
    "https://localhost.example.com/api/v1",
  ])("blocks the unapproved host in %s", (value) => {
    expect(preview(value).baseUrl).toBeNull()
    expect(preview(value).problem).toContain("not approved")
  })

  it.each([
    "http://localhost:8000/api/v1",
    "http://127.0.0.1:8000/api/v1",
    "http://[::1]:8000/api/v1",
  ])("allows the local API %s", (value) => {
    expect(preview(value)).toEqual({ isPreview: true, baseUrl: value, problem: null })
  })

  it("allows only the exact approved remote host, over https", () => {
    const host = "staging-api.example.com"
    expect(preview(`https://${host}/api/v1`, host).baseUrl).not.toBeNull()
    expect(preview(`http://${host}/api/v1`, host).baseUrl).toBeNull()
    expect(preview(`https://x.${host}/api/v1`, host).baseUrl).toBeNull()
    expect(preview(`https://${host}/api/v1`, "*").baseUrl).toBeNull()
  })
})

describe("what the viewer sees", () => {
  it("replaces the whole app with a clear message when blocked", () => {
    render(
      <PreviewEnvironmentGuard config={preview(PRODUCTION)}>
        <p>the application</p>
      </PreviewEnvironmentGuard>
    )
    expect(screen.getByRole("alert")).toHaveTextContent("Preview API is not configured")
    expect(screen.getByRole("alert")).toHaveTextContent("PRODUCTION")
    expect(screen.queryByText("the application")).not.toBeInTheDocument()
  })

  it("labels a working preview with the API host", () => {
    render(
      <PreviewEnvironmentGuard config={preview("http://localhost:8000/api/v1")}>
        <p>the application</p>
      </PreviewEnvironmentGuard>
    )
    expect(screen.getByTestId("preview-banner")).toHaveTextContent(
      "PREVIEW · API: localhost:8000"
    )
    expect(screen.getByText("the application")).toBeInTheDocument()
  })

  it("adds nothing outside a preview", () => {
    const { container } = render(
      <PreviewEnvironmentGuard
        config={resolveApiConfig({
          deployEnv: "production",
          apiUrl: PRODUCTION,
          previewApiHost: undefined,
        })}
      >
        <p>the application</p>
      </PreviewEnvironmentGuard>
    )
    expect(container.innerHTML).toBe("<p>the application</p>")
  })
})

describe("the shared API client", () => {
  afterEach(() => {
    vi.unstubAllEnvs()
    vi.resetModules()
  })

  async function client(env: Record<string, string>) {
    vi.resetModules()
    for (const [name, value] of Object.entries(env)) vi.stubEnv(name, value)
    return (await import("@/lib/api-client")).apiClient
  }

  it("sends nothing at all from a blocked preview", async () => {
    const apiClient = await client({
      NEXT_PUBLIC_DEPLOY_ENV: "preview",
      NEXT_PUBLIC_API_URL: PRODUCTION,
    })
    const adapter = vi.fn()
    apiClient.defaults.adapter = adapter
    await expect(apiClient.get("/auth/me")).rejects.toThrow(
      "Preview API is not configured"
    )
    await expect(
      apiClient.post("/auth/login", {}, { baseURL: PRODUCTION })
    ).rejects.toThrow("Preview API is not configured")
    expect(adapter).not.toHaveBeenCalled()
    expect(apiClient.defaults.baseURL).toBeUndefined()
  })

  it("uses the production URL unchanged in a production build", async () => {
    const apiClient = await client({
      NEXT_PUBLIC_DEPLOY_ENV: "production",
      NEXT_PUBLIC_API_URL: PRODUCTION,
    })
    expect(apiClient.defaults.baseURL).toBe(PRODUCTION)
  })
})

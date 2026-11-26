/** Which backend the web app may talk to.
 *
 * Production and local development behave as they always have. A Vercel
 * PREVIEW build fails closed: it never falls back to a default and never talks
 * to the production API. It accepts only
 *  - an API on the viewer's own machine (localhost), or
 *  - the one https host named in NEXT_PUBLIC_PREVIEW_API_HOST.
 */

export const LOCAL_DEFAULT_API_URL = "http://localhost:8000/api/v1"

/** Never allowed from a preview, even if named as the preview host. Add every
 * other address of the production API here (custom domains, aliases). */
export const PRODUCTION_API_HOSTS: readonly string[] = ["ayushwellness-oms.onrender.com"]

const LOOPBACK_HOSTS = new Set(["localhost", "127.0.0.1", "[::1]"])

export interface ApiConfig {
  isPreview: boolean
  /** null: no API may be called. */
  baseUrl: string | null
  /** Why no API may be called (preview only). */
  problem: string | null
}

export interface ApiEnvironment {
  /** "preview" on a Vercel preview build (see next.config.ts). */
  deployEnv: string | undefined
  apiUrl: string | undefined
  previewApiHost: string | undefined
}

const normaliseHost = (host: string) => host.trim().toLowerCase().replace(/\.+$/, "")

export function resolveApiConfig(env: ApiEnvironment): ApiConfig {
  if (env.deployEnv !== "preview") {
    return {
      isPreview: false,
      baseUrl: env.apiUrl ?? LOCAL_DEFAULT_API_URL,
      problem: null,
    }
  }
  const blocked = (problem: string): ApiConfig => ({
    isPreview: true,
    baseUrl: null,
    problem,
  })

  const value = env.apiUrl?.trim()
  if (!value) {
    return blocked("NEXT_PUBLIC_API_URL is not set for this preview.")
  }
  let url: URL
  try {
    url = new URL(value)
  } catch {
    return blocked("NEXT_PUBLIC_API_URL must be a full http(s) address.")
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") {
    return blocked("NEXT_PUBLIC_API_URL must be a full http(s) address.")
  }
  if (url.username || url.password) {
    return blocked("NEXT_PUBLIC_API_URL must not contain a user name or password.")
  }
  const host = normaliseHost(url.hostname)
  if (PRODUCTION_API_HOSTS.includes(host)) {
    return blocked("NEXT_PUBLIC_API_URL points to the PRODUCTION API.")
  }
  if (!LOOPBACK_HOSTS.has(host)) {
    const approved = normaliseHost(env.previewApiHost ?? "")
    if (host !== approved) {
      return blocked(
        `The host "${host}" is not approved for previews. Use a local API, or set NEXT_PUBLIC_PREVIEW_API_HOST to a non-production host.`
      )
    }
    if (url.protocol !== "https:") {
      return blocked("A remote preview API must use https.")
    }
  }
  return { isPreview: true, baseUrl: value, problem: null }
}

// Each variable is read by its full name so Next.js can inline it at build time.
export const apiConfig: ApiConfig = resolveApiConfig({
  deployEnv: process.env.NEXT_PUBLIC_DEPLOY_ENV,
  apiUrl: process.env.NEXT_PUBLIC_API_URL,
  previewApiHost: process.env.NEXT_PUBLIC_PREVIEW_API_HOST,
})
